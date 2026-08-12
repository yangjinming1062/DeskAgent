from typing import ClassVar

from .._provider_errors import raise_for_provider_response
from ..base import ProviderConfig, STTProvider, STTResult
from ..http import get_http


class GrokSTTProvider(STTProvider):
    """STT via xAI's unary ``POST /v1/stt``.

    Wire shape (multipart, per xAI REST reference):
        ``file`` — audio bytes
        ``url``  — alternative to ``file`` (server-side download)

    Optional form fields: ``audio_format`` / ``sample_rate`` (only for raw
    formats like pcm/mulaw/alaw — container formats auto-detect), ``language``,
    ``format``, ``multichannel`` / ``channels``, ``diarize``, ``keyterm``,
    ``filler_words``, ``vad_threshold``.

    Notes:
    - No ``model`` field — xAI's STT endpoint has no model selector; the
      service uses grok-transcribe under the hood (mirrors the streaming
      WebSocket endpoint's ``audio.input.transcription.model`` default).
    - ``language`` is unused here (xAI auto-detects) so the corresponding
      parameter is a no-op kept for the :class:`STTProvider` ABC contract.
    - Response: ``{"text", "language", "duration", "words"?}``.
    """

    provider_name = "grok"
    # grok-transcribe is the underlying model (per the streaming STT docs);
    # the unary endpoint doesn't take a selector, so the default is purely
    # metadata for the registry mirror and never sent on the wire.
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {"stt": "grok-transcribe"}
    DEFAULT_CONTEXT_TOKENS: ClassVar[dict[str, int]] = {"stt": 8_000}

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._client = get_http(config.base_url, config.api_key)

    async def transcribe(self, audio: bytes, *, mime_type: str = "audio/wav", language: str = "auto") -> STTResult:
        # xAI auto-detects container formats from the file header — the
        # multipart filename just needs to be plausible.
        if "wav" in mime_type:
            ext = "wav"
        elif "mpeg" in mime_type or "mp3" in mime_type:
            ext = "mp3"
        elif "ogg" in mime_type:
            ext = "ogg"
        elif "flac" in mime_type:
            ext = "flac"
        elif "m4a" in mime_type or "mp4" in mime_type:
            ext = "m4a"
        else:
            ext = "bin"

        files = {"file": (f"audio.{ext}", audio, mime_type)}

        resp = await self._client.post("/stt", files=files)
        body = raise_for_provider_response(resp, family=self.provider_name, model=self.config.model)
        text = body.get("text", "")
        return STTResult(text=text.strip(), raw=body)
