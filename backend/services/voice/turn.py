"""语音回合编排：复用 run_chat_turn，把流式输出切子句/句成段，流式合成 TTS 按块下发。"""

import asyncio
import contextlib
from collections import deque
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from components import SETTINGS, get_logger, session_scope
from modules.conversation import Message
from modules.system import ChatMessageRequest, ChatRequest

from ..chat import load_user_settings, run_chat_turn
from ..gateway import RuntimeSession
from ..llm import (
    AudioChunk,
    MissingLlmConfigError,
    resolve_user_llm_config,
    synthesize_speech,
    synthesize_speech_stream,
)
from .audio import encode_audio_frame
from .segmenter import SentenceSegmenter

if TYPE_CHECKING:
    from .session import VoiceSession

logger = get_logger(__name__)


class VoiceEmitter:
    """run_chat_turn 的 sink：chunk 喂切句器成段入队，完成/错误帧收尾；工具帧与 message.start 忽略。"""

    def __init__(self, queue: asyncio.Queue[str | None], segmenter: SentenceSegmenter) -> None:
        self._queue = queue
        self._segmenter = segmenter
        self.sentence_parts: list[str] = []
        self.completed: dict | None = None
        self.error_message: str | None = None

    async def send_json(self, data: dict) -> None:
        frame_type = data.get("type")
        if frame_type == "chunk":
            await self._enqueue(self._segmenter.feed(data.get("content", "")))
        elif frame_type == "bubble.break":
            await self._enqueue(self._segmenter.flush())
        elif frame_type == "message.complete":
            self.completed = data
            await self._enqueue(self._segmenter.flush())
            await self._queue.put(None)
        elif frame_type == "error":
            self.error_message = data.get("message")
            await self._enqueue(self._segmenter.flush())
            await self._queue.put(None)

    async def _enqueue(self, segments: list[str]) -> None:
        for seg in segments:
            self.sentence_parts.append(seg)
            await self._queue.put(seg)

    @property
    def spoken_text(self) -> str:
        """已成段文本的累积；打断时的部分落库与 turn.end 文本都取它。"""
        return "".join(self.sentence_parts)


class _SegmentPump:
    """单段合成泵：把合成源（流式或整段）泵进队列，发送侧严格按段序消费——预取窗口内多段
    并行打开、按序发送。错误以异常对象入队；正常结束补 None 哨兵。"""

    def __init__(self, source: AsyncIterator[AudioChunk], text: str) -> None:
        self.text = text
        self.queue: asyncio.Queue[AudioChunk | BaseException | None] = asyncio.Queue(16)
        self.task = asyncio.create_task(self._run(source))

    async def _run(self, source: AsyncIterator[AudioChunk]) -> None:
        try:
            async for chunk in source:
                await self.queue.put(chunk)
        except asyncio.CancelledError:
            # 显式关闭挂起的合成源生成器，供应商流连接即刻回收而非等 GC。
            with contextlib.suppress(Exception):
                await source.aclose()
            raise
        except BaseException as exc:
            await self.queue.put(exc)
        finally:
            with contextlib.suppress(asyncio.QueueFull):
                self.queue.put_nowait(None)


class VoiceTurn:
    """转写后的一轮对话：LLM 流式输出 → 子句/句成段 TTS 流式合成 → 按序分块下发；支持打断收尾。"""

    def __init__(self, session: "VoiceSession", turn_id: str, text: str) -> None:
        self.session = session
        self.turn_id = turn_id
        self.text = text
        self.segments_sent = 0

    async def run(self) -> None:
        s = self.session
        try:
            await s.send_json("llm.start", turn_id=self.turn_id)
            queue: asyncio.Queue[str | None] = asyncio.Queue()
            emitter = VoiceEmitter(queue, SentenceSegmenter(SETTINGS.voice_segment_max_chars, SETTINGS.voice_clause_min_chars))
            speaker = asyncio.create_task(self._speak_loop(queue, emitter))

            req = ChatRequest(session_id=str(s.conversation_id), message=ChatMessageRequest(role="user", content=self.text))
            async with session_scope() as db:
                llm_config = await resolve_user_llm_config(db, s.user.id)
                user_settings = await load_user_settings(db, s.user.id)

            runtime = RuntimeSession(
                conversation_id=s.conversation_id,
                settings={"agent.reasoning_effort": "none", "reasoning": "none"},
                kind="voice",
            )

            try:
                await run_chat_turn(req, llm_config, user_settings, s.user.id, emitter, runtime=runtime)
            except asyncio.CancelledError:
                speaker.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await speaker
                await self._finalize_interrupted(emitter)
                return
            except Exception as e:
                logger.exception("voice turn failed", extra={"user_id": s.user.id, "turn_id": self.turn_id})
                speaker.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await speaker
                await s.send_json("turn.error", stage="llm", code="failed", message=str(e)[:200])
                return

            with contextlib.suppress(asyncio.CancelledError, Exception):
                await queue.put(None)
                await speaker
            if emitter.completed is None:
                # run_chat_turn 以 error 帧收尾（流式中途供应商失败等），管线自身不抛。
                await s.send_json("turn.error", stage="llm", code="failed", message=emitter.error_message or "LLM 调用失败")
                return
            complete = emitter.completed
            await s.send_json(
                "turn.end",
                turn_id=self.turn_id,
                interrupted=False,
                text=complete.get("text", ""),
                affect=complete.get("affect"),
                media=complete.get("media") or [],
                usage=complete.get("usage"),
            )
        finally:
            s.note_turn_finished()

    async def _segment_stream(self, text: str) -> AsyncIterator[AudioChunk]:
        if SETTINGS.voice_streaming_enabled:
            async for chunk in synthesize_speech_stream(self.session.user.id, text, self.session.voice_id):
                yield chunk
        else:
            result = await synthesize_speech(self.session.user.id, text, self.session.voice_id)
            yield AudioChunk(result.audio, result.mime)

    async def _speak_loop(self, queue: asyncio.Queue[str | None], emitter: VoiceEmitter) -> None:
        """滑动窗口预取开流、按段序发送；单段失败跳过音频不阻塞后续段。"""
        prefetch = max(1, SETTINGS.voice_tts_prefetch)
        pumps: deque[_SegmentPump] = deque()
        current: _SegmentPump | None = None
        ended = False
        try:
            while pumps or not ended:
                while not ended and len(pumps) < prefetch:
                    try:
                        item = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    if item is None:
                        ended = True
                        break
                    if pump := await self._start_pump(item):
                        pumps.append(pump)
                if not pumps:
                    if ended:
                        break
                    item = await queue.get()
                    if item is None:
                        break
                    if pump := await self._start_pump(item):
                        pumps.append(pump)
                    continue
                current = pumps.popleft()
                await self._drain_pump(current)
                current = None
        finally:
            # 正在排空的泵已弹出 deque，取消/异常路径必须一并取消，否则泵任务挂着无人消费的供应商流连接。
            if current is not None:
                current.task.cancel()
            for pump in pumps:
                pump.task.cancel()

    async def _start_pump(self, text: str) -> _SegmentPump | None:
        s = self.session
        if not s.tts_bucket.allow():
            await s.send_json("turn.error", stage="tts", code="rate_limited", message="语音合成限流，本句已跳过")
            return None
        return _SegmentPump(self._segment_stream(text), text)

    async def _drain_pump(self, pump: _SegmentPump) -> None:
        """按序消费单段：首块前失败该段静默跳过；裸 PCM 攒块下发，段结束残余冲发并标段末；
        首块后失败冲发已缓冲部分（段序号已固定）。"""
        s = self.session
        seg_index = self.segments_sent
        saw_error = False
        buf = bytearray()
        buf_mime = ""
        buf_rate = 0
        target = 0
        while True:
            item = await pump.queue.get()
            if item is None:
                break
            if isinstance(item, BaseException):
                saw_error = True
                await self._note_segment_failure(item)
                break
            if item.mime != buf_mime or item.sample_rate != buf_rate:
                if buf:
                    await self._send_audio(bytes(buf), buf_mime, seg_index, buf_rate, final=False)
                    buf.clear()
                buf_mime, buf_rate = item.mime, item.sample_rate
                target = item.sample_rate * 2 * SETTINGS.voice_downlink_chunk_ms // 1000 if item.mime == "audio/pcm" else 0
            buf.extend(item.audio)
            if target and len(buf) >= target:
                await self._send_audio(bytes(buf), buf_mime, seg_index, buf_rate, final=False)
                buf.clear()
        # 以 buf_mime 已被赋值作为「至少收到一块音频」的标志；空 mime 时该段被静默跳过
        if buf_mime:
            # 尾残余冲发并标段末；尾恰好为空（末块刚好填满攒块尺寸）也发空载荷末块，
            # 保持「每段以末块收尾」无条件成立（打断中的段除外）。
            await self._send_audio(bytes(buf), buf_mime, seg_index, buf_rate, final=True)
            self.segments_sent = seg_index + 1
        elif not saw_error:
            logger.warning("voice tts segment produced no audio", extra={"user_id": s.user.id, "turn_id": self.turn_id})

    async def _send_audio(self, audio: bytes, mime: str, seg_index: int, sample_rate: int, *, final: bool) -> None:
        s = self.session
        await s.send_audio(encode_audio_frame(audio, mime, seg_index, sample_rate, final=final))
        s.note_downlink_audio()

    async def _note_segment_failure(self, exc: BaseException) -> None:
        s = self.session
        if isinstance(exc, MissingLlmConfigError):
            await s.send_json("turn.error", stage="tts", code="no_provider", message="未配置语音合成供应商")
        else:
            logger.warning("voice tts segment failed", extra={"user_id": s.user.id, "turn_id": self.turn_id, "error": str(exc)})
            await s.send_json("turn.error", stage="tts", code="segment_failed", message="本句语音合成失败，已跳过")

    async def _finalize_interrupted(self, emitter: VoiceEmitter) -> None:
        s = self.session
        partial = emitter.spoken_text
        if partial:
            # 打断只落已成段文本，与已下发音频对齐；shield 防止收尾期间再次取消丢行。
            async def _persist() -> None:
                async with session_scope() as db:
                    db.add(Message(conversation_id=s.conversation_id, role="assistant", content=partial))
                    await db.commit()

            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.shield(_persist())
        await s.send_json("session.interrupted", turn_id=self.turn_id, stopped_seg_index=self.segments_sent)
        await s.send_json("turn.end", turn_id=self.turn_id, interrupted=True, text=partial)
