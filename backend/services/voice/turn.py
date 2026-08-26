"""语音回合编排：复用 run_chat_turn，把流式输出按句合成 TTS 并即时下发二进制帧。"""

import asyncio
import contextlib
from collections import deque
from typing import TYPE_CHECKING

from components import SETTINGS, get_logger, session_scope
from modules.conversation import Message
from modules.system import ChatMessageRequest, ChatRequest

from ..chat import load_user_settings, run_chat_turn
from ..llm import MissingLlmConfigError, TTSResult, resolve_user_llm_config, synthesize_speech
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
        """已完成整句的累积文本；打断时的部分落库与 turn.end 文本都取它。"""
        return "".join(self.sentence_parts)


class VoiceTurn:
    """转写后的一轮对话：LLM 流式输出 → 按句 TTS 预取合成 → 按序下发；支持打断收尾。"""

    def __init__(self, session: "VoiceSession", turn_id: str, text: str) -> None:
        self.session = session
        self.turn_id = turn_id
        self.text = text
        self.segments_sent = 0

    async def run(self) -> None:
        s = self.session
        await s.send_json("llm.start", turn_id=self.turn_id)
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        emitter = VoiceEmitter(queue, SentenceSegmenter(SETTINGS.voice_segment_max_chars))
        speaker = asyncio.create_task(self._speak_loop(queue, emitter))

        req = ChatRequest(session_id=str(s.conversation_id), message=ChatMessageRequest(role="user", content=self.text))
        async with session_scope() as db:
            llm_config = await resolve_user_llm_config(db, s.user.id)
            user_settings = await load_user_settings(db, s.user.id)

        try:
            await run_chat_turn(req, llm_config, user_settings, s.user.id, emitter)
        except asyncio.CancelledError:
            speaker.cancel()
            with contextlib.suppress(Exception):
                await speaker
            await self._finalize_interrupted(emitter)
            return
        except Exception as e:
            logger.exception("voice turn failed", extra={"user_id": s.user.id, "turn_id": self.turn_id})
            speaker.cancel()
            with contextlib.suppress(Exception):
                await speaker
            await s.send_json("turn.error", stage="llm", code="failed", message=str(e)[:200])
            return

        with contextlib.suppress(Exception):
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

    async def _speak_loop(self, queue: asyncio.Queue[str | None], emitter: VoiceEmitter) -> None:
        """滑动窗口预取合成、按 seg_index 顺序发送；单段失败跳过不阻塞后续段。"""
        s = self.session
        prefetch = max(1, SETTINGS.voice_tts_prefetch)
        window: deque[asyncio.Task[tuple[str, TTSResult | None]]] = deque()
        ended = False
        try:
            while window or not ended:
                while not ended and len(window) < prefetch:
                    try:
                        item = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    if item is None:
                        ended = True
                        break
                    window.append(asyncio.create_task(self._synth_segment(item)))
                if not window:
                    if ended:
                        break
                    item = await queue.get()
                    if item is None:
                        break
                    window.append(asyncio.create_task(self._synth_segment(item)))
                    continue
                text, result = await window.popleft()
                if result is None:
                    continue
                await s.send_json("tts.segment", turn_id=self.turn_id, seg_index=self.segments_sent, text=text)
                await s.send_audio(encode_audio_frame(result.audio, result.mime, self.segments_sent))
                self.segments_sent += 1
        except asyncio.CancelledError:
            for task in window:
                task.cancel()
            raise

    async def _synth_segment(self, text: str) -> tuple[str, TTSResult | None]:
        s = self.session
        if not s.tts_bucket.allow():
            await s.send_json("turn.error", stage="tts", code="rate_limited", message="语音合成限流，本句已跳过")
            return text, None
        try:
            return text, await synthesize_speech(s.user.id, text, s.voice_id)
        except MissingLlmConfigError:
            await s.send_json("turn.error", stage="tts", code="no_provider", message="未配置语音合成供应商")
        except Exception as e:
            logger.warning("voice tts segment failed", extra={"user_id": s.user.id, "turn_id": self.turn_id, "error": str(e)})
            await s.send_json("turn.error", stage="tts", code="segment_failed", message="本句语音合成失败，已跳过")
        return text, None

    async def _finalize_interrupted(self, emitter: VoiceEmitter) -> None:
        s = self.session
        partial = emitter.spoken_text
        if partial:
            # 打断只落已完成整句，与已下发音频对齐；shield 防止收尾期间再次取消丢行。
            async def _persist() -> None:
                async with session_scope() as db:
                    db.add(Message(conversation_id=s.conversation_id, role="assistant", content=partial))
                    await db.commit()

            with contextlib.suppress(Exception):
                await asyncio.shield(_persist())
        await s.send_json("session.interrupted", turn_id=self.turn_id, stopped_seg_index=self.segments_sent)
        await s.send_json("turn.end", turn_id=self.turn_id, interrupted=True, text=partial)
