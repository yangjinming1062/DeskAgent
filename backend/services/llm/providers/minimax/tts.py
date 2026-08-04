import logging
from typing import ClassVar

from ..base import ProviderConfig
from ..base import TTSProvider
from ..base import TTSResult
from ..base import VoiceDesignResult
from ..http import get_http
from ._errors import extract_minimax_audio
from ._errors import raise_for_minimax_response

logger = logging.getLogger(__name__)


class MiniMaxTTSProvider(TTSProvider):
    """TTS via MiniMax's synchronous ``POST /v1/t2a_v2``.

    Wire shape: ``{model, text, voice_setting:{voice_id, speed, vol}, audio_setting:{format, sample_rate}}``.
    Returns hex-encoded audio in ``data.audio`` (we hex-decode back to bytes).
    Async long-text variant (``/v1/t2a_async_v2``) is not wrapped here — chat
    replies are short enough to fit in the 10 000-char sync limit.
    """

    provider_name = "minimax"
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"tts": "speech-2.8-hd"}
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

    async def synthesize(
        self,
        text: str,
        *,
        voice: str = "",
        fmt: str = "mp3",
        speed: float | None = None,
    ) -> TTSResult:
        chosen_voice = voice or self.VOICE_CATALOG[0]["id"]
        if voice != chosen_voice:
            logger.info("minimax tts: substituted voice", extra={"requested": voice, "used": chosen_voice})
        payload: dict = {
            "model": self.config.model,
            "text": text,
            "voice_setting": {
                "voice_id": chosen_voice,
                "speed": speed if speed is not None else 1.0,
                "vol": 1.0,
                "pitch": 0,
            },
            "audio_setting": {
                "audio_sample_rate": 32000,
                "bitrate": 128000,
                "format": fmt,
                "channel": 1,
            },
        }
        resp = await self._client.post("/v1/t2a_v2", json=payload)
        body = raise_for_minimax_response(resp, provider="minimax", model=self.config.model)
        audio = extract_minimax_audio(body)
        mime = "audio/mpeg" if fmt == "mp3" else f"audio/{fmt}"
        return TTSResult(audio=audio, mime=mime)

    async def design_voice(
        self,
        prompt: str,
        *,
        preview_text: str = "",
    ) -> VoiceDesignResult:
        payload: dict = {
            "prompt": prompt,
            "preview_text": preview_text or "你好，我是你的桌面伙伴。",
        }
        resp = await self._client.post("/v1/voice_design", json=payload)
        body = raise_for_minimax_response(resp, provider="minimax", model=self.config.model)
        voice_id = body.get("voice_id", "")
        if not voice_id:
            raise RuntimeError("MiniMax voice design returned no voice_id")
        trial_hex = body.get("trial_audio", "")
        if not trial_hex:
            raise RuntimeError("MiniMax voice design returned no trial_audio")
        return VoiceDesignResult(voice_id=voice_id, trial_audio=bytes.fromhex(trial_hex), trial_audio_mime="audio/mpeg")
