import enum
from abc import ABC
from abc import abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Literal

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

    def __init__(self, config: ProviderConfig):
        self.config = config

    def raw_client(self) -> "AsyncOpenAI | None":
        """Default: no OpenAI SDK client. Chat subclasses override this when
        the wire protocol is OpenAI-compatible. ``client_for_service`` uses
        this to detect whether the legacy AsyncOpenAI path is reachable."""
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
    ):
        super().__init__(message)
        self.status_code = status_code
        self.body = body or {}
        self.provider = provider
        self.model = model


# ── Chat ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ChatStreamEvent:
    type: Literal["delta", "tool_call", "usage", "done", "error"]
    text: str = ""
    reasoning: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    usage: dict | None = None
    finish_reason: str = ""
    raw: Any = None


@dataclass(frozen=True)
class ChatResult:
    text: str
    usage: dict | None = None
    raw: Any = None


class ChatProvider(BaseProvider):
    service_type: ServiceType = ServiceType.llm

    @abstractmethod
    def raw_client(self) -> AsyncOpenAI | None:
        """Return the underlying cached ``AsyncOpenAI`` if the provider can be
        reached via the OpenAI SDK; ``None`` for non-OpenAI-compatible providers."""

    @abstractmethod
    async def stream(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        **params: Any,
    ) -> AsyncIterator[ChatStreamEvent]: ...

    @abstractmethod
    async def complete(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        **params: Any,
    ) -> ChatResult: ...


# ── Image generation ───────────────────────────────────────────────────


@dataclass(frozen=True)
class ImageGenRequest:
    prompt: str
    n: int = 1
    size: str | None = None
    aspect_ratio: str | None = None
    quality: str | None = None
    reference_image: str | None = None
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

    async def download(self, asset: VideoAsset) -> bytes:
        """Default downloader. Plain httpx GET — no Authorization header.

        ``get_http`` would attach the provider's Bearer token to whatever
        URL we pass it, but ``asset.download_url`` is typically a
        third-party CDN (MiniMax files are hosted off ``api.minimaxi.com``).
        Sending the API key to a CDN host leaks it; download anonymously.
        """
        import httpx

        async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=10.0)) as client:
            resp = await client.get(asset.download_url)
            resp.raise_for_status()
            return resp.content

    async def generate_and_wait(
        self,
        req: VideoGenRequest,
        *,
        timeout: float,
        interval: float,
    ) -> VideoJobStatus:
        """Submit + poll until terminal status. Tool uses this with a finite
        timeout; long-running jobs continue in the background after timeout."""
        import asyncio
        import time

        job = await self.submit(req)
        deadline = time.monotonic() + timeout
        while True:
            if job.status in ("succeeded", "failed"):
                return job
            if time.monotonic() >= deadline:
                return job
            await asyncio.sleep(interval)
            job = await self.poll(job.task_id)


# ── TTS / STT ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TTSResult:
    audio: bytes
    mime: str


class TTSProvider(BaseProvider):
    service_type: ServiceType = ServiceType.tts

    @abstractmethod
    async def synthesize(
        self,
        text: str,
        *,
        voice: str = "",
        fmt: str = "mp3",
        speed: float | None = None,
    ) -> TTSResult: ...


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