import json
import logging
from collections.abc import AsyncIterator
from typing import ClassVar

import httpx

from ..base import AudioChunk, ProviderConfig, TTSProvider, TTSResult, VoiceDesignResult, pick_catalog_voice
from ..http import get_http
from ._errors import extract_minimax_audio, raise_for_minimax_response, raise_for_minimax_stream_event

logger = logging.getLogger(__name__)

# 流式 SSE 事件间隙可能超过共享客户端的默认 read 超时（按整段请求时长设定），流式请求单独放宽。
_STREAM_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=10.0)


class MiniMaxTTSProvider(TTSProvider):
    """通过 MiniMax 同步 POST /v1/t2a_v2 提供 TTS（{model,text,voice_setting:{voice_id,speed,vol},audio_setting:{format,sample_rate}}，data.audio 为 hex 编码音频，本模块解码回字节）；流式走同端点 stream=true SSE（事件 data.audio hex + data.status 1/2，末事件默认重复携带聚合音频需显式排除）；异步长文本 /v1/t2a_async_v2 未封装，chat 回复足够短，落在 10000 字符同步上限内。"""

    provider_name = "minimax"
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"tts": "speech-2.8-hd"}
    DEFAULT_CONTEXT_TOKENS: ClassVar[dict[str, int]] = {"tts": 8_000}
    SUPPORTS_SYNTH_STREAM = True
    VOICE_DESIGN_GUIDE = """\
用一段文字描述你想要的音色，描述越具体效果越好。建议涵盖：
• 性别与年龄：如"沉稳可靠的中年男性"、"专业播音腔的中年女性"
• 音色质感：如"低沉富有磁性"、"清亮柔和"
• 情绪语气：如"温柔自信"、"慵懒俏皮"
• 语速节奏：如"语速时快时慢"、"缓慢沉稳"
preview_text 为试听文本——设计完成后会用它合成一段示例音频供你试听。\
"""
    VOICE_CATALOG: ClassVar[list[dict]] = [
        {"id": "female-shaonv", "label": "少女音", "gender": "female", "language": "zh", "tags": ["少女", "温柔", "甜", "活泼", "女"], "description": "清甜的少女音，活泼温柔。"},
        {"id": "female-yujie", "label": "御姐音", "gender": "female", "language": "zh", "tags": ["御姐", "清冷", "成熟", "沉稳", "女"], "description": "清冷成熟的御姐音。"},
        {"id": "female-chengshu", "label": "知性女声", "gender": "female", "language": "zh", "tags": ["知性", "温柔", "成熟", "女", "稳重"], "description": "温柔知性的成熟女声。"},
        {"id": "female-mengyao", "label": "萌丫音", "gender": "female", "language": "zh", "tags": ["萌", "可爱", "甜", "少女", "女"], "description": "软萌可爱的少女音。"},
        {"id": "male-qn-qingse", "label": "青涩少年", "gender": "male", "language": "zh", "tags": ["少年", "青涩", "清新", "男", "正太"], "description": "清新青涩的少年音。"},
        {"id": "male-qn-jingying", "label": "精英男声", "gender": "male", "language": "zh", "tags": ["精英", "沉稳", "成熟", "磁性", "男"], "description": "沉稳干练的精英男声。"},
    ]

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._client = get_http(config.base_url, config.api_key)

    async def synthesize(self, text: str, *, voice: str = "", fmt: str = "mp3", speed: float | None = None) -> TTSResult:
        chosen_voice = pick_catalog_voice(voice, self.VOICE_CATALOG)
        if voice != chosen_voice:
            logger.info("minimax tts: substituted voice", extra={"requested": voice, "used": chosen_voice})
        payload: dict = {
            "model": self.config.model,
            "text": text,
            "voice_setting": {"voice_id": chosen_voice, "speed": speed if speed is not None else 1.0, "vol": 1.0, "pitch": 0},
            "audio_setting": {"audio_sample_rate": 32000, "bitrate": 128000, "format": fmt, "channel": 1},
        }
        resp = await self._client.post("/v1/t2a_v2", json=payload)
        body = raise_for_minimax_response(resp, provider="minimax", model=self.config.model)
        audio = extract_minimax_audio(body)
        mime = "audio/mpeg" if fmt == "mp3" else f"audio/{fmt}"
        return TTSResult(audio=audio, mime=mime, voice=chosen_voice)

    async def synthesize_stream(self, text: str, *, voice: str = "", speed: float | None = None) -> AsyncIterator[AudioChunk]:
        chosen_voice = pick_catalog_voice(voice, self.VOICE_CATALOG)
        if voice != chosen_voice:
            logger.info("minimax tts: substituted voice", extra={"requested": voice, "used": chosen_voice})
        payload: dict = {
            "model": self.config.model,
            "text": text,
            "stream": True,
            "stream_options": {"exclude_aggregated_audio": True},
            "voice_setting": {"voice_id": chosen_voice, "speed": speed if speed is not None else 1.0, "vol": 1.0, "pitch": 0},
            "audio_setting": {"audio_sample_rate": 32000, "format": "pcm", "channel": 1},
        }
        async with self._client.stream("POST", "/v1/t2a_v2", json=payload, timeout=_STREAM_TIMEOUT) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                try:
                    body = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                if not isinstance(body, dict):
                    continue
                raise_for_minimax_stream_event(body, provider="minimax", model=self.config.model)
                hex_audio = (body.get("data") or {}).get("audio") or ""
                if hex_audio:
                    yield AudioChunk(bytes.fromhex(hex_audio), "audio/pcm", sample_rate=32000)

    async def design_voice(self, prompt: str, *, preview_text: str = "") -> VoiceDesignResult:
        payload: dict = {"prompt": prompt, "preview_text": preview_text or "你好，我是你的桌面伙伴。"}
        resp = await self._client.post("/v1/voice_design", json=payload)
        body = raise_for_minimax_response(resp, provider="minimax", model=self.config.model)
        voice_id = body.get("voice_id", "")
        if not voice_id:
            raise RuntimeError("MiniMax voice design returned no voice_id")
        trial_hex = body.get("trial_audio", "")
        if not trial_hex:
            raise RuntimeError("MiniMax voice design returned no trial_audio")
        return VoiceDesignResult(voice_id=voice_id, trial_audio=bytes.fromhex(trial_hex), trial_audio_mime="audio/mpeg")
