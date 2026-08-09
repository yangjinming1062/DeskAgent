import base64
import logging
from typing import ClassVar

from .._provider_errors import raise_for_provider_response
from ..base import ProviderConfig
from ..base import TTSProvider
from ..base import TTSResult
from ..http import get_http
from ._parts import iter_parts

logger = logging.getLogger(__name__)


class GeminiTTSProvider(TTSProvider):
    """TTS via Gemini's ``generateContent`` with ``responseModalities: ["AUDIO"]``."""

    provider_name = "gemini"
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"tts": "gemini-2.5-flash-preview-tts"}
    DEFAULT_CONTEXT_TOKENS: ClassVar[dict[str, int]] = {"tts": 8_000}
    # Tag zh/en so language-keyword scoring applies even though every voice is multilingual.
    VOICE_CATALOG: ClassVar[list[dict]] = [
        {"id": "Kore", "label": "Kore", "gender": "neutral", "language": "multi", "tags": ["坚定", "温柔", "温暖", "自然", "中性", "zh", "en"], "description": "坚定温暖的音色。"},
        {"id": "Zephyr", "label": "Zephyr", "gender": "neutral", "language": "multi", "tags": ["明亮", "bright", "zh", "en"], "description": "明亮的音色。"},
        {"id": "Puck", "label": "Puck", "gender": "neutral", "language": "multi", "tags": ["欢快", "活泼", "轻快", "俏皮", "zh", "en"], "description": "欢快轻快的音色。"},
        {
            "id": "Charon",
            "label": "Charon",
            "gender": "male",
            "language": "multi",
            "tags": ["信息丰富", "低沉", "沉稳", "男", "磁性", "zh", "en"],
            "description": "信息丰富、低沉稳重的男声。",
        },
        {
            "id": "Fenrir",
            "label": "Fenrir",
            "gender": "male",
            "language": "multi",
            "tags": ["易兴奋", "果断", "有力", "男", "强势", "zh", "en"],
            "description": "易兴奋、果断有力的男声。",
        },
        {"id": "Leda", "label": "Leda", "gender": "female", "language": "multi", "tags": ["青春", "明亮", "女", "清", "zh", "en"], "description": "明亮青春的女声。"},
        {"id": "Orus", "label": "Orus", "gender": "neutral", "language": "multi", "tags": ["沉稳", "稳重", "坚定", "zh", "en"], "description": "沉稳坚定的音色。"},
        {
            "id": "Aoede",
            "label": "Aoede",
            "gender": "female",
            "language": "multi",
            "tags": ["柔和", "Breezy", "温柔", "女", "zh", "en"],
            "description": "柔和轻快（Breezy）的女声。",
        },
        {"id": "Callirrhoe", "label": "Callirrhoe", "gender": "neutral", "language": "multi", "tags": ["随和", "轻松", "zh", "en"], "description": "随和轻松的音色。"},
        {"id": "Autonoe", "label": "Autonoe", "gender": "neutral", "language": "multi", "tags": ["明亮", "清亮", "zh", "en"], "description": "明亮清亮的音色。"},
        {"id": "Enceladus", "label": "Enceladus", "gender": "neutral", "language": "multi", "tags": ["气声", "轻柔", "breathy", "zh", "en"], "description": "气声轻柔的音色。"},
        {"id": "Iapetus", "label": "Iapetus", "gender": "neutral", "language": "multi", "tags": ["清晰", "clear", "zh", "en"], "description": "清晰的音色。"},
        {"id": "Umbriel", "label": "Umbriel", "gender": "neutral", "language": "multi", "tags": ["随和", "温和", "zh", "en"], "description": "随和温和的音色。"},
        {"id": "Algieba", "label": "Algieba", "gender": "neutral", "language": "multi", "tags": ["平滑", "smooth", "zh", "en"], "description": "平滑的音色。"},
        {"id": "Despina", "label": "Despina", "gender": "neutral", "language": "multi", "tags": ["平滑", "柔和", "zh", "en"], "description": "平滑柔和的音色。"},
        {"id": "Erinome", "label": "Erinome", "gender": "neutral", "language": "multi", "tags": ["清亮", "清晰", "zh", "en"], "description": "清亮的音色。"},
        {"id": "Algenib", "label": "Algenib", "gender": "neutral", "language": "multi", "tags": ["沙哑", "gravelly", "zh", "en"], "description": "沙哑（Gravelly）的音色。"},
        {"id": "Rasalgethi", "label": "Rasalgethi", "gender": "neutral", "language": "multi", "tags": ["信息丰富", "沉稳", "zh", "en"], "description": "信息丰富的音色。"},
        {"id": "Laomedeia", "label": "Laomedeia", "gender": "neutral", "language": "multi", "tags": ["欢快", "活泼", "zh", "en"], "description": "欢快的音色。"},
        {"id": "Achernar", "label": "Achernar", "gender": "neutral", "language": "multi", "tags": ["柔和", "soft", "zh", "en"], "description": "柔和的音色。"},
        {"id": "Alnilam", "label": "Alnilam", "gender": "neutral", "language": "multi", "tags": ["坚定", "沉稳", "zh", "en"], "description": "坚定的音色。"},
        {"id": "Schedar", "label": "Schedar", "gender": "neutral", "language": "multi", "tags": ["平稳", "均匀", "zh", "en"], "description": "平稳均匀的音色。"},
        {"id": "Gacrux", "label": "Gacrux", "gender": "neutral", "language": "multi", "tags": ["成熟", "mature", "zh", "en"], "description": "成熟的音色。"},
        {"id": "Pulcherrima", "label": "Pulcherrima", "gender": "neutral", "language": "multi", "tags": ["明快", "积极", "zh", "en"], "description": "明快积极的音色。"},
        {"id": "Achird", "label": "Achird", "gender": "neutral", "language": "multi", "tags": ["友好", "亲切", "friendly", "zh", "en"], "description": "友好的音色。"},
        {
            "id": "Zubenelgenubi",
            "label": "Zubenelgenubi",
            "gender": "neutral",
            "language": "multi",
            "tags": ["随意", "轻松", "casual", "zh", "en"],
            "description": "随意轻松的音色。",
        },
        {"id": "Vindemiatrix", "label": "Vindemiatrix", "gender": "neutral", "language": "multi", "tags": ["温和", "gentle", "zh", "en"], "description": "温和的音色。"},
        {"id": "Sadachbia", "label": "Sadachbia", "gender": "neutral", "language": "multi", "tags": ["活泼", "lively", "zh", "en"], "description": "活泼的音色。"},
        {"id": "Sadaltager", "label": "Sadaltager", "gender": "neutral", "language": "multi", "tags": ["知识渊博", "沉稳", "zh", "en"], "description": "知识渊博的音色。"},
        {"id": "Sulafat", "label": "Sulafat", "gender": "neutral", "language": "multi", "tags": ["偏高", "明亮", "zh", "en"], "description": "偏高的音色。"},
    ]

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._client = get_http(config.base_url, config.api_key, auth_header={"x-goog-api-key": "{api_key}"})

    async def synthesize(
        self,
        text: str,
        *,
        voice: str = "",
        fmt: str = "mp3",  # noqa: ARG002 — abstract TTS contract; gemini ignores
        speed: float | None = None,  # noqa: ARG002 — abstract TTS contract; gemini ignores
    ) -> TTSResult:
        chosen_voice = voice or self.VOICE_CATALOG[0]["id"]
        if voice != chosen_voice:
            logger.info("gemini tts: substituted voice", extra={"requested": voice, "used": chosen_voice})
        payload = {
            "contents": [{"parts": [{"text": text}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": chosen_voice}},
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
                    voice=chosen_voice,
                )

        raise RuntimeError(f"Gemini TTS returned no audio: {body}")
