"""语音会话：/api/voice/ws 的服务端状态机。全双工——上行 PCM 持续接收，服务端 VAD 判定
话语起止（断句即整段转写），回放下行期间判插话（判真即取消回合）；回合事件只走本 WS、不进 outbox。
"""

import asyncio
import contextlib
import json
import secrets
import time

from components import SESSION_LOCAL, SETTINGS, get_logger
from fastapi import WebSocket
from modules.auth import User
from modules.conversation import Conversation

from ..llm import MissingLlmConfigError, resolve, resolve_provider_chain, transcribe_audio
from .audio import pcm_to_wav
from .turn import VoiceTurn
from .vad import BargeInDetector, EnergyVad, VadEvent

logger = get_logger(__name__)

MAX_BINARY_FRAME_BYTES = 64 * 1024

# 同一用户同时只允许一个语音会话；新连接顶掉旧连接。
ACTIVE_SESSIONS: dict[int, "VoiceSession"] = {}


class _MinuteBucket:
    """进程内每分钟固定窗口计数；slowapi 绑定 Request，WS 通道用不了，语音自带独立配额。"""

    def __init__(self, limit: int) -> None:
        self.limit = max(1, limit)
        self._window = 0
        self._count = 0

    def allow(self) -> bool:
        window = int(time.monotonic() // 60)
        if window != self._window:
            self._window = window
            self._count = 0
        if self._count >= self.limit:
            return False
        self._count += 1
        return True


_STT_BUCKETS: dict[int, _MinuteBucket] = {}
_TTS_BUCKETS: dict[int, _MinuteBucket] = {}


class VoiceSession:
    def __init__(self, websocket: WebSocket, user: User) -> None:
        self.ws = websocket
        self.user = user
        self.conversation_id: int | None = None
        self.voice_id = ""
        self.sample_rate = SETTINGS.voice_uplink_sample_rate
        self.turn_seq = 0
        self.turn_task: asyncio.Task | None = None
        self.vad = EnergyVad(
            self.sample_rate,
            floor_ratio=SETTINGS.voice_vad_floor_ratio,
            onset_ms=SETTINGS.voice_speech_onset_ms,
            endpoint_ms=SETTINGS.voice_endpoint_silence_ms,
            max_seconds=SETTINGS.voice_max_utterance_seconds,
        )
        self.bargein = BargeInDetector(
            self.sample_rate,
            floor_ratio=SETTINGS.voice_vad_floor_ratio,
            echo_ratio=SETTINGS.voice_bargein_energy_ratio,
            onset_ms=SETTINGS.voice_bargein_onset_ms,
            deafen_ms=SETTINGS.voice_bargein_deafen_ms,
        )
        self.preroll_bytes = SETTINGS.voice_vad_preroll_ms * self.sample_rate * 2 // 1000
        self.max_utterance_bytes = SETTINGS.voice_max_utterance_seconds * self.sample_rate * 2
        self.tail = bytearray()
        self.utterance = bytearray()
        self.speaking = False
        self.downlink_active = False
        self.last_activity = time.monotonic()
        self.closed_reason: str | None = None
        self.stt_bucket = _STT_BUCKETS.setdefault(user.id, _MinuteBucket(SETTINGS.voice_stt_rate_limit_per_minute))
        self.tts_bucket = _TTS_BUCKETS.setdefault(user.id, _MinuteBucket(SETTINGS.voice_tts_rate_limit_per_minute))

    async def send_json(self, op: str, **payload) -> None:
        with contextlib.suppress(Exception):
            await self.ws.send_json({"v": 1, "op": op, **payload})

    async def send_audio(self, frame: bytes) -> None:
        with contextlib.suppress(Exception):
            await self.ws.send_bytes(frame)

    async def run(self) -> None:
        await self.ws.accept()
        prior = ACTIVE_SESSIONS.get(self.user.id)
        if prior is not None:
            await prior.shutdown("duplicate_session")
        ACTIVE_SESSIONS[self.user.id] = self
        logger.info("voice session opened", extra={"user_id": self.user.id})
        watchdog = asyncio.create_task(self._watchdog())
        try:
            while True:
                msg = await self.ws.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                if msg.get("text") is not None:
                    await self._on_text(msg["text"])
                elif msg.get("bytes") is not None:
                    # 空闲判定按语音活动计：持续上行本身不算活动（见 _watchdog）。
                    await self._on_bytes(msg["bytes"])
        finally:
            watchdog.cancel()
            if self.turn_task and not self.turn_task.done():
                self.turn_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await self.turn_task
            if ACTIVE_SESSIONS.get(self.user.id) is self:
                ACTIVE_SESSIONS.pop(self.user.id, None)
            logger.info("voice session closed", extra={"user_id": self.user.id, "reason": self.closed_reason})

    async def shutdown(self, reason: str) -> None:
        if self.closed_reason is not None:
            return
        self.closed_reason = reason
        await self.send_json("session.closed", reason=reason)
        with contextlib.suppress(Exception):
            await self.ws.close()

    async def _watchdog(self) -> None:
        hard_deadline = time.monotonic() + SETTINGS.voice_session_hard_timeout_seconds
        try:
            while True:
                await asyncio.sleep(5.0)
                now = time.monotonic()
                if now >= hard_deadline:
                    await self.shutdown("hard_timeout")
                    return
                if now - self.last_activity >= SETTINGS.voice_session_idle_timeout_seconds:
                    await self.shutdown("idle_timeout")
                    return
        except asyncio.CancelledError:
            raise

    async def _on_text(self, raw: str) -> None:
        self.last_activity = time.monotonic()
        try:
            msg = json.loads(raw)
        except ValueError:
            msg = None
        if not isinstance(msg, dict):
            await self.send_json("session.error", code="protocol", message="invalid control frame")
            return
        op = msg.get("op")
        if op == "session.start":
            await self._on_session_start(msg)
        elif op == "interrupt":
            await self._interrupt()
        elif op == "session.end":
            await self.shutdown(str(msg.get("reason") or "client_end"))
        else:
            await self.send_json("session.error", code="protocol", message=f"unknown op {op!r}")

    async def _on_session_start(self, msg: dict) -> None:
        if self.conversation_id is not None:
            await self.send_json("session.error", code="protocol", message="session already started")
            return
        try:
            sample_rate = int(msg.get("sample_rate") or SETTINGS.voice_uplink_sample_rate)
        except (TypeError, ValueError):
            sample_rate = 0
        if sample_rate != SETTINGS.voice_uplink_sample_rate:
            await self.send_json("session.error", code="unsupported_sample_rate", message=f"sample_rate must be {SETTINGS.voice_uplink_sample_rate}")
            await self.shutdown("unsupported_sample_rate")
            return
        session_id = str(msg.get("session_id") or "")
        async with SESSION_LOCAL() as db:
            conv = await Conversation.by_session_id(db, session_id, user_id=self.user.id)
            stt_ready = bool(await resolve_provider_chain(db, self.user.id, "stt"))
            tts_chain = await resolve_provider_chain(db, self.user.id, "tts")
        if conv is None:
            await self.send_json("session.error", code="protocol", message=f"session not found: {session_id!r}")
            return
        if not stt_ready:
            await self.send_json("session.error", code="no_stt_provider", message="未配置云端语音识别供应商，无法语音通话")
            await self.shutdown("no_stt_provider")
            return
        if not tts_chain:
            await self.send_json("session.error", code="no_tts_provider", message="未配置云端语音合成供应商，无法语音通话")
            await self.shutdown("no_tts_provider")
            return
        self.conversation_id = conv.id
        self.voice_id = str(msg.get("voice") or "")
        tts_stream = SETTINGS.voice_streaming_enabled and any(resolve(config.service_type, config.provider_name).SUPPORTS_SYNTH_STREAM for config in tts_chain)
        await self.send_json(
            "session.ready",
            voice_session_id=secrets.token_hex(8),
            session_id=str(conv.id),
            tts_voice=self.voice_id or None,
            caps={"tts_stream": tts_stream},
        )

    async def _on_bytes(self, data: bytes) -> None:
        if len(data) > MAX_BINARY_FRAME_BYTES:
            await self.shutdown("frame_too_large")
            return
        if self.conversation_id is None:
            return
        if self.speaking:
            room = self.max_utterance_bytes - len(self.utterance)
            if room > 0:
                self.utterance.extend(data[:room])
            for event in self.vad.feed(data):
                if event is VadEvent.SPEECH_END:
                    await self._finish_utterance(truncated=False)
                    return
                if event is VadEvent.OVERFLOW:
                    await self._finish_utterance(truncated=True)
                    return
            return
        # 非说话态：先进预滚环（插话语与起说瞬态的回看缓冲）。
        self.tail.extend(data)
        if len(self.tail) > self.preroll_bytes:
            del self.tail[: len(self.tail) - self.preroll_bytes]
        if self.downlink_active and self.turn_task and not self.turn_task.done():
            # 回放下行期间不喂常规 VAD（回声会被误判成话语），喂插话判别器。
            if self.bargein.feed(data):
                await self._interrupt()
                self._begin_utterance()
            return
        for event in self.vad.feed(data):
            if event is VadEvent.SPEECH_START:
                self._begin_utterance()
                self.last_activity = time.monotonic()

    def _begin_utterance(self) -> None:
        self.speaking = True
        self.utterance = bytearray(self.tail)
        self.tail.clear()
        # 插话路径下 VAD 未被喂过（处于 idle 态），外部直接置入说话态；常规路径重复置入无害。
        self.vad.force_speaking()

    async def _finish_utterance(self, truncated: bool) -> None:
        self.speaking = False
        pcm = bytes(self.utterance)
        self.utterance = bytearray()
        self.last_activity = time.monotonic()
        if not pcm:
            return
        # 回合仍进行中（如思考期开口）：成段话语视为隐式打断。
        if self.turn_task and not self.turn_task.done():
            await self._interrupt()
        if not self.stt_bucket.allow():
            await self.send_json("turn.error", stage="asr", code="rate_limited", message="语音识别限流，请稍候再试")
            return
        self.turn_seq += 1
        self.turn_task = asyncio.create_task(self._run_turn(str(self.turn_seq), pcm, truncated))

    async def _interrupt(self) -> None:
        task = self.turn_task
        self.turn_task = None
        if task is not None and not task.done():
            task.cancel()
            # CancelledError 是 BaseException，suppress(Exception) 接不住——取消落在转写/思考期时
            # 任务以取消态结束，await 抛出的 CancelledError 会逃逸杀死会话循环，必须显式列出。
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    def note_downlink_audio(self) -> None:
        """回合每发一块下行音频时调用：标记下发中并驱动致盲窗/回声底估计。"""
        self.downlink_active = True
        self.bargein.on_downlink_audio()

    def note_turn_finished(self) -> None:
        """回合终态（正常/打断/失败）由 VoiceTurn 收尾时调用。"""
        self.downlink_active = False
        self.bargein.reset()
        self.last_activity = time.monotonic()

    async def _run_turn(self, turn_id: str, pcm: bytes, truncated: bool) -> None:
        duration_ms = len(pcm) // 2 * 1000 // max(1, self.sample_rate)
        try:
            wav = pcm_to_wav(pcm, self.sample_rate)
            try:
                text = (await transcribe_audio(self.user.id, wav, "audio/wav")).strip()
            except MissingLlmConfigError:
                await self.send_json("turn.error", stage="asr", code="no_provider", message="未配置云端语音识别供应商")
                return
            except Exception as e:
                logger.warning("voice asr failed", extra={"user_id": self.user.id, "turn_id": turn_id, "error": str(e)})
                await self.send_json("turn.error", stage="asr", code="all_providers_failed", message="没听清，请再说一次")
                return
            if not text:
                await self.send_json("asr.skipped", reason="empty_transcript")
                return
            await self.send_json("asr.final", turn_id=turn_id, text=text, duration_ms=duration_ms, truncated=truncated)
            await VoiceTurn(self, turn_id, text).run()
        except asyncio.CancelledError:
            raise


async def handle_voice_websocket(websocket: WebSocket, user: User) -> None:
    await VoiceSession(websocket, user).run()
