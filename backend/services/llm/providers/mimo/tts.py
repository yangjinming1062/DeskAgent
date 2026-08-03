import base64
from typing import ClassVar

from components import MAX_VOICE_DESIGN_PROMPT_CHARS
from openai import AsyncOpenAI

from ..base import ProviderConfig
from ..base import TTSProvider
from ..base import TTSResult
from ..base import VoiceDesignResult
from ..http import get_async_client

_VOICEDESIGN_MODEL = "mimo-v2.5-tts-voicedesign"
_VOICEDESIGN_PREFIX = "mimo_voicedesign:"


class MiMoTTSProvider(TTSProvider):
    """TTS via MiMo's ``audio={...}`` extension on Chat Completions.

    Wire shape:
        POST /v1/chat/completions
        body: messages=[{user,""},{assistant,text}], audio={format,voice}
    """

    provider_name = "mimo"
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"tts": "mimo-v2.5-tts"}
    VOICE_DESIGN_GUIDE = """\
关键维度（不需要面面俱到）：
• 性别与年龄：如"二十多岁的年轻女性"、"五十岁的中年男性"
• 音色/质感：如"丝滑醇厚、带磁性"、"清亮柔和"
• 情绪/语气：如"温柔自信"、"慵懒俏皮"
• 语速/节奏：如"语速偏快、像连珠炮"、"缓慢沉稳"
中英文均可，1-4 句即可。避免矛盾特征（如"稚嫩童声+CEO气场"）、音质效果词（混响、回声）和模糊词（"普通的""正常的"）。\
"""
    VOICE_CATALOG: ClassVar[list[dict]] = [
        {"id": "mimo_default", "label": "默认音色", "gender": "neutral", "language": "multi", "tags": ["默认", "温柔", "自然", "中性"], "description": "MiMo 默认音色，自然温和。"},
        {"id": "冰糖", "label": "冰糖", "gender": "female", "language": "zh", "tags": ["冰糖", "温柔", "甜", "女", "中文"], "description": "温柔甜美的中文女声。"},
        {"id": "茉莉", "label": "茉莉", "gender": "female", "language": "zh", "tags": ["茉莉", "清亮", "自然", "女", "中文"], "description": "清亮自然的中文女声。"},
        {"id": "苏打", "label": "苏打", "gender": "male", "language": "zh", "tags": ["苏打", "清爽", "男", "中文"], "description": "清爽干净的中文男声。"},
        {"id": "白桦", "label": "白桦", "gender": "male", "language": "zh", "tags": ["白桦", "沉稳", "低沉", "男", "中文"], "description": "沉稳低沉的中文男声。"},
        {
            "id": "Mia",
            "label": "Mia",
            "gender": "female",
            "language": "en",
            "tags": ["Mia", "bright", "warm", "female", "english"],
            "description": "Bright warm English female voice.",
        },
        {
            "id": "Chloe",
            "label": "Chloe",
            "gender": "female",
            "language": "en",
            "tags": ["Chloe", "cheerful", "lively", "female", "english"],
            "description": "Cheerful lively English female voice.",
        },
        {"id": "Milo", "label": "Milo", "gender": "male", "language": "en", "tags": ["Milo", "friendly", "male", "english"], "description": "Friendly English male voice."},
        {"id": "Dean", "label": "Dean", "gender": "male", "language": "en", "tags": ["Dean", "calm", "deep", "male", "english"], "description": "Calm deep English male voice."},
    ]

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        self._client: AsyncOpenAI = get_async_client(config.api_key, config.base_url)

    def raw_client(self) -> AsyncOpenAI | None:
        return self._client

    async def synthesize(
        self,
        text: str,
        *,
        voice: str = "",
        fmt: str = "mp3",
        speed: float | None = None,
    ) -> TTSResult:
        if voice and voice.startswith(_VOICEDESIGN_PREFIX):
            design_prompt = voice[len(_VOICEDESIGN_PREFIX) :]
            if not design_prompt.strip():
                raise ValueError("voice design prompt is empty")
            # Same cap the JSON-RPC design path enforces; the REST POST
            # /api/media/tts path can carry an unbounded ``voice`` form field
            # otherwise, which the voicedesign model would happily bill for.
            if len(design_prompt) > MAX_VOICE_DESIGN_PROMPT_CHARS:
                raise ValueError(f"prompt exceeds {MAX_VOICE_DESIGN_PROMPT_CHARS} chars")
            audio_kwargs: dict = {"format": fmt, "optimize_text_preview": True}
            messages = [
                {"role": "user", "content": design_prompt},
                {"role": "assistant", "content": text},
            ]
            model = _VOICEDESIGN_MODEL
        else:
            audio_kwargs = {"format": fmt}
            if voice:
                audio_kwargs["voice"] = voice
            messages = [
                {"role": "user", "content": ""},
                {"role": "assistant", "content": text},
            ]
            model = self.config.model
        response = await self._client.chat.completions.create(
            model=model,
            messages=messages,
            audio=audio_kwargs,
        )
        choice = response.choices[0] if response.choices else None
        if not choice or not getattr(choice.message, "audio", None):
            raise RuntimeError("MiMo TTS returned no audio")
        mime = "audio/mpeg" if fmt == "mp3" else f"audio/{fmt}"
        return TTSResult(audio=base64.b64decode(choice.message.audio.data), mime=mime)

    async def design_voice(
        self,
        prompt: str,
        *,
        preview_text: str = "",
    ) -> VoiceDesignResult:
        response = await self._client.chat.completions.create(
            model=_VOICEDESIGN_MODEL,
            messages=[
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": preview_text or "你好，我是你的桌面伙伴。"},
            ],
            audio={"format": "mp3", "optimize_text_preview": True},
        )
        choice = response.choices[0] if response.choices else None
        if not choice or not getattr(choice.message, "audio", None):
            raise RuntimeError("MiMo voice design returned no audio")
        return VoiceDesignResult(
            voice_id=f"{_VOICEDESIGN_PREFIX}{prompt}",
            trial_audio=base64.b64decode(choice.message.audio.data),
            trial_audio_mime="audio/mpeg",
        )
