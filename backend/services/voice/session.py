"""语音会话：/api/voice/ws 的服务端状态机。半双工回合制——上行 PCM 攒 utterance，
utterance 结束触发 ASR→LLM→TTS 回合（services/voice/turn.py），回合事件只走本 WS、不进 outbox。
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

from ..llm import MissingLlmConfigError, resolve_provider_chain, transcribe_audio
from .audio import pcm_to_wav
from .turn import VoiceTurn

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
        self.utterance_buf = bytearray()
        self.utterance_active = False
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
                self.last_activity = time.monotonic()
                if msg["type"] == "websocket.disconnect":
                    break
                if msg.get("text") is not None:
                    await self._on_text(msg["text"])
                elif msg.get("bytes") is not None:
                    await self._on_bytes(msg["bytes"])
        finally:
            watchdog.cancel()
            if self.turn_task and not self.turn_task.done():
                self.turn_task.cancel()
                with contextlib.suppress(Exception):
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
        elif op == "utterance.start":
            self.utterance_active = True
            self.utterance_buf.clear()
        elif op == "utterance.end":
            await self._end_utterance(truncated=bool(msg.get("truncated")))
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
            tts_ready = bool(await resolve_provider_chain(db, self.user.id, "tts"))
        if conv is None:
            await self.send_json("session.error", code="protocol", message=f"session not found: {session_id!r}")
            return
        if not stt_ready:
            await self.send_json("session.error", code="no_stt_provider", message="未配置云端语音识别供应商，无法语音通话")
            await self.shutdown("no_stt_provider")
            return
        if not tts_ready:
            await self.send_json("session.error", code="no_tts_provider", message="未配置云端语音合成供应商，无法语音通话")
            await self.shutdown("no_tts_provider")
            return
        self.conversation_id = conv.id
        self.voice_id = str(msg.get("voice") or "")
        await self.send_json(
            "session.ready",
            voice_session_id=secrets.token_hex(8),
            session_id=str(conv.id),
            tts_voice=self.voice_id or None,
        )

    async def _on_bytes(self, data: bytes) -> None:
        if len(data) > MAX_BINARY_FRAME_BYTES:
            await self.shutdown("frame_too_large")
            return
        if not self.utterance_active:
            self.utterance_active = True
        max_bytes = SETTINGS.voice_max_utterance_seconds * self.sample_rate * 2
        room = max_bytes - len(self.utterance_buf)
        if room > 0:
            self.utterance_buf.extend(data[:room])

    async def _end_utterance(self, truncated: bool) -> None:
        self.utterance_active = False
        pcm = bytes(self.utterance_buf)
        self.utterance_buf.clear()
        if not pcm:
            return
        # 回合进行中收到 utterance.end 且客户端未先 interrupt：视为隐式打断（说话即插话）。
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
            with contextlib.suppress(Exception):
                await task

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
