import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal

from openai import AsyncOpenAI


class ServiceType(str, enum.Enum):
    llm = "llm"
    stt = "stt"
    tts = "tts"
    image_gen = "image_gen"
    video_gen = "video_gen"


@dataclass(frozen=True)
class ProviderConfig:
    base_url: str
    api_key: str
    model: str
    service_type: ServiceType
    provider_name: str
    extra: dict[str, Any] = field(default_factory=dict)


class BaseProvider(ABC):
    """Root of the provider tree. Concrete subclasses declare ``service_type``
    (a :class:`ServiceType`) and ``provider_name`` (str) as class attributes;
    per-service ABCs below add the protocol-specific abstract methods."""

    service_type: ServiceType = ServiceType.llm
    provider_name: str = ""
    # Which prompt-guidance family this provider's models belong to. Drives
    # tool-use enforcement + execution-discipline block selection in
    # ``system_prompt.build_system_prompt_parts``. ``"openai"`` is the default
    # (OpenAI-compatible discipline suits mimo / minimax / zhipu / any
    # OpenAI-protocol endpoint); Google models override to ``"google"``.
    PROMPT_FAMILY: ClassVar[str] = "openai"
    # Per-capability default MODEL_NAME published by this provider. Mirrored
    # into ``registry._PROVIDER_DEFAULT_MODELS`` at register() time so the
    # capability resolver can pull defaults without importing each provider
    # class. Subclasses populate the keys for capabilities they implement;
    # absent keys fall back to ``SETTINGS.<svc>_model_name``.
    DEFAULT_MODELS: ClassVar[dict[str, str]] = {}
    # Per-capability default CONTEXT_TOKENS paired with DEFAULT_MODELS rows.
    DEFAULT_CONTEXT_TOKENS: ClassVar[dict[str, int]] = {}
    # Vision MODEL_NAME override when it differs from DEFAULT_MODELS["llm"]
    # (e.g. mimo: ``mimo-v2.5`` for vision vs ``mimo-v2.5-pro`` for text).
    DEFAULT_VISION_MODELS: ClassVar[dict[str, str]] = {}

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config

    def raw_client(self) -> "AsyncOpenAI | None":
        """Default: no OpenAI SDK client. Chat subclasses override this when
        the wire protocol is OpenAI-compatible; callers use it to detect
        whether the AsyncOpenAI path is reachable."""
        return None


class ProviderError(Exception):
    """Provider-level error. Fields align with error_classifier readers:

    - ``status_code`` is read by ``_extract_status_code`` (int or None)
    - ``body`` is read by ``_extract_error_body`` (dict, may be empty)
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        body: dict | None = None,
        provider: str = "",
        model: str = "",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body or {}
        self.provider = provider
        self.model = model


# ── Chat ────────────────────────────────────────────────────────────────


class ChatProvider(BaseProvider):
    service_type: ServiceType = ServiceType.llm

    # True → image_url content parts accepted (on a vision variant if the
    # text model is text-only — see DEFAULT_VISION_MODELS).
    supports_vision: ClassVar[bool] = False

    @abstractmethod
    def raw_client(self) -> AsyncOpenAI | None:
        """Return the underlying cached ``AsyncOpenAI`` if the provider can be
        reached via the OpenAI SDK; ``None`` for non-OpenAI-compatible providers."""


# ── Image generation ───────────────────────────────────────────────────


@dataclass(frozen=True)
class ImageGenRequest:
    prompt: str
    n: int = 1
    size: str | None = None
    aspect_ratio: str | None = None
    quality: str | None = None
    reference_image: str | None = None
    # Secondary reference (e.g. a presentation/style ref alongside the identity
    # anchor). Only consumed by providers with supports_multiple_reference_images.
    secondary_reference_image: str | None = None
    response_format: Literal["b64", "url"] = "b64"


@dataclass(frozen=True)
class ImageAsset:
    b64: str | None = None
    url: str | None = None
    mime: str = "image/png"


@dataclass(frozen=True)
class ImageGenResult:
    images: list[ImageAsset]
    model: str
    raw: Any = None


class ImageGenProvider(BaseProvider):
    service_type: ServiceType = ServiceType.image_gen

    # True → provider consumes ``reference_image`` natively (image-to-image).
    # False → skipped for reference-image requests (no image→text→image).
    supports_reference_image: ClassVar[bool] = False
    # True → provider also consumes ``secondary_reference_image`` (dual i2i).
    # When False, the chain filters this provider out for dual-ref requests,
    # degrading to single-ref providers rather than silently dropping the image.
    supports_multiple_reference_images: ClassVar[bool] = False

    @abstractmethod
    async def generate(self, req: ImageGenRequest) -> ImageGenResult: ...


# ── Video generation ───────────────────────────────────────────────────


@dataclass(frozen=True)
class VideoGenRequest:
    prompt: str
    duration: int = 6
    resolution: str = "768P"
    first_frame_image: str | None = None
    aspect_ratio: str | None = None
    model: str | None = None


@dataclass(frozen=True)
class VideoJobStatus:
    task_id: str
    status: Literal["queued", "processing", "succeeded", "failed"]
    file_id: str | None = None
    # Providers whose success path returns the file URL inline (no separate
    # ``files/retrieve`` step — MiniMax H3 v2) populate this so the job
    # worker can skip the second hop. ``None`` means "call fetch(file_id)".
    download_url: str | None = None
    error: str | None = None
    raw: Any = None


@dataclass(frozen=True)
class VideoAsset:
    download_url: str
    content_type: str
    size: int | None = None
    expires_at: float | None = None


class VideoGenProvider(BaseProvider):
    service_type: ServiceType = ServiceType.video_gen

    @abstractmethod
    async def submit(self, req: VideoGenRequest) -> VideoJobStatus: ...

    @abstractmethod
    async def poll(self, task_id: str) -> VideoJobStatus: ...

    @abstractmethod
    async def fetch(self, file_id: str) -> VideoAsset: ...


# ── TTS / STT ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TTSResult:
    audio: bytes
    mime: str
    # Substituted voice id after provider fallback; surfaces in X-Voice-Used.
    voice: str = ""


@dataclass(frozen=True)
class VoiceDesignResult:
    voice_id: str
    trial_audio: bytes
    trial_audio_mime: str


class TTSProvider(BaseProvider):
    service_type: ServiceType = ServiceType.tts

    VOICE_CATALOG: ClassVar[list[dict]] = []

    # None → provider doesn't support voice design. A non-empty guide string
    # → provider supports it; the string is shown to users as a writing guide.
    VOICE_DESIGN_GUIDE: ClassVar[str | None] = None

    @abstractmethod
    async def synthesize(
        self,
        text: str,
        *,
        voice: str = "",
        fmt: str = "mp3",
        speed: float | None = None,
    ) -> TTSResult: ...

    async def design_voice(
        self,
        prompt: str,
        *,
        preview_text: str = "",
    ) -> VoiceDesignResult:
        raise NotImplementedError(f"{self.provider_name} does not support voice design")


@dataclass(frozen=True)
class STTResult:
    text: str
    raw: Any = None


class STTProvider(BaseProvider):
    service_type: ServiceType = ServiceType.stt

    @abstractmethod
    async def transcribe(
        self,
        audio: bytes,
        *,
        mime_type: str = "audio/wav",
        language: str = "auto",
    ) -> STTResult: ...
