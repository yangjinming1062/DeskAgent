import base64
from typing import ClassVar

from .._provider_errors import raise_for_provider_response
from ..base import ProviderConfig
from ..base import TTSProvider
from ..base import TTSResult
from ..http import get_http
from ._parts import iter_parts


class GeminiTTSProvider(TTSProvider):
    """TTS via Gemini's ``generateContent`` with ``responseModalities: ["AUDIO"]``."""

    provider_name = "gemini"
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"tts": "gemini-2.5-flash-preview-tts"}
    VOICE_CATALOG: ClassVar[list[dict]] = [
        {"id": "Kore", "label": "Kore", "gender": "neutral", "language": "multi", "tags": ["坚定", "温柔", "温暖", "自然", "中性"], "description": "坚定温暖的音色。"},
        {"id": "Zephyr", "label": "Zephyr", "gender": "neutral", "language": "multi", "tags": ["明亮", "bright"], "description": "明亮的音色。"},
        {"id": "Puck", "label": "Puck", "gender": "neutral", "language": "multi", "tags": ["欢快", "活泼", "轻快", "俏皮"], "description": "欢快轻快的音色。"},
        {"id": "Charon", "label": "Charon", "gender": "male", "language": "multi", "tags": ["信息丰富", "低沉", "沉稳", "男", "磁性"], "description": "信息丰富、低沉稳重的男声。"},
        {"id": "Fenrir", "label": "Fenrir", "gender": "male", "language": "multi", "tags": ["易兴奋", "果断", "有力", "男", "强势"], "description": "易兴奋、果断有力的男声。"},
        {"id": "Leda", "label": "Leda", "gender": "female", "language": "multi", "tags": ["青春", "明亮", "女", "清"], "description": "明亮青春的女声。"},
        {"id": "Orus", "label": "Orus", "gender": "neutral", "language": "multi", "tags": ["沉稳", "稳重", "坚定"], "description": "沉稳坚定的音色。"},
        {"id": "Aoede", "label": "Aoede", "gender": "female", "language": "multi", "tags": ["柔和", "Breezy", "温柔", "女"], "description": "柔和轻快（Breezy）的女声。"},
        {"id": "Callirrhoe", "label": "Callirrhoe", "gender": "neutral", "language": "multi", "tags": ["随和", "轻松"], "description": "随和轻松的音色。"},
        {"id": "Autonoe", "label": "Autonoe", "gender": "neutral", "language": "multi", "tags": ["明亮", "清亮"], "description": "明亮清亮的音色。"},
        {"id": "Enceladus", "label": "Enceladus", "gender": "neutral", "language": "multi", "tags": ["气声", "轻柔", "breathy"], "description": "气声轻柔的音色。"},
        {"id": "Iapetus", "label": "Iapetus", "gender": "neutral", "language": "multi", "tags": ["清晰", "clear"], "description": "清晰的音色。"},
        {"id": "Umbriel", "label": "Umbriel", "gender": "neutral", "language": "multi", "tags": ["随和", "温和"], "description": "随和温和的音色。"},
        {"id": "Algieba", "label": "Algieba", "gender": "neutral", "language": "multi", "tags": ["平滑", "smooth"], "description": "平滑的音色。"},
        {"id": "Despina", "label": "Despina", "gender": "neutral", "language": "multi", "tags": ["平滑", "柔和"], "description": "平滑柔和的音色。"},
        {"id": "Erinome", "label": "Erinome", "gender": "neutral", "language": "multi", "tags": ["清亮", "清晰"], "description": "清亮的音色。"},
        {"id": "Algenib", "label": "Algenib", "gender": "neutral", "language": "multi", "tags": ["沙哑", "gravelly"], "description": "沙哑（Gravelly）的音色。"},
        {"id": "Rasalgethi", "label": "Rasalgethi", "gender": "neutral", "language": "multi", "tags": ["信息丰富", "沉稳"], "description": "信息丰富的音色。"},
        {"id": "Laomedeia", "label": "Laomedeia", "gender": "neutral", "language": "multi", "tags": ["欢快", "活泼"], "description": "欢快的音色。"},
        {"id": "Achernar", "label": "Achernar", "gender": "neutral", "language": "multi", "tags": ["柔和", "soft"], "description": "柔和的音色。"},
        {"id": "Alnilam", "label": "Alnilam", "gender": "neutral", "language": "multi", "tags": ["坚定", "沉稳"], "description": "坚定的音色。"},
        {"id": "Schedar", "label": "Schedar", "gender": "neutral", "language": "multi", "tags": ["平稳", "均匀"], "description": "平稳均匀的音色。"},
        {"id": "Gacrux", "label": "Gacrux", "gender": "neutral", "language": "multi", "tags": ["成熟", "mature"], "description": "成熟的音色。"},
        {"id": "Pulcherrima", "label": "Pulcherrima", "gender": "neutral", "language": "multi", "tags": ["明快", "积极"], "description": "明快积极的音色。"},
        {"id": "Achird", "label": "Achird", "gender": "neutral", "language": "multi", "tags": ["友好", "亲切", "friendly"], "description": "友好的音色。"},
        {"id": "Zubenelgenubi", "label": "Zubenelgenubi", "gender": "neutral", "language": "multi", "tags": ["随意", "轻松", "casual"], "description": "随意轻松的音色。"},
        {"id": "Vindemiatrix", "label": "Vindemiatrix", "gender": "neutral", "language": "multi", "tags": ["温和", "gentle"], "description": "温和的音色。"},
        {"id": "Sadachbia", "label": "Sadachbia", "gender": "neutral", "language": "multi", "tags": ["活泼", "lively"], "description": "活泼的音色。"},
        {"id": "Sadaltager", "label": "Sadaltager", "gender": "neutral", "language": "multi", "tags": ["知识渊博", "沉稳"], "description": "知识渊博的音色。"},
        {"id": "Sulafat", "label": "Sulafat", "gender": "neutral", "language": "multi", "tags": ["偏高", "明亮"], "description": "偏高的音色。"},
    ]

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        self._client = get_http(config.base_url, config.api_key, auth_header={"x-goog-api-key": "{api_key}"})

    async def synthesize(
        self,
        text: str,
        *,
        voice: str = "",
        fmt: str = "mp3",
        speed: float | None = None,
    ) -> TTSResult:
        payload = {
            "contents": [{"parts": [{"text": text}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice or self.VOICE_CATALOG[0]["id"]}},
                },
            },
        }

        resp = await self._client.post(f"/v1beta/models/{self.config.model}:generateContent", json=payload)
        body = raise_for_provider_response(resp, family="gemini", model=self.config.model)

        for part in iter_parts(body):
            inline = part.get("inlineData")
            if inline and inline.get("data"):
                return TTSResult(
                    audio=base64.b64decode(inline["data"]),
                    mime=inline.get("mimeType", "audio/wav"),
                )

        raise RuntimeError(f"Gemini TTS returned no audio: {body}")
