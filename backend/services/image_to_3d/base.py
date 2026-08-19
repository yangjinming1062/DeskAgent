from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Literal


@dataclass(frozen=True)
class Model3DJob:
    job_id: str


@dataclass(frozen=True)
class Model3DAsset:
    kind: str  # "glb" | "obj" | ...
    url: str
    preview_image_url: str | None = None


@dataclass(frozen=True)
class Model3DPollResult:
    status: Literal["queued", "in_progress", "completed", "failed"]
    # Provider's own 0-100 progress signal; 0 = unknown (orchestration
    # interpolates by elapsed time instead).
    progress: int = 0
    assets: tuple[Model3DAsset, ...] = ()
    error: str | None = None


class ImageTo3DError(Exception):
    """Base error for image-to-3D service and providers."""

    def __init__(self, message: str, *, status_code: int | None = None, body: dict | None = None, provider: str = "", model: str = "") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body or {}
        self.provider = provider
        self.model = model


class ImageTo3DProvider(ABC):
    """Text-to-3D generation provider ABC (the image-to-3D entry is retained
    but the generation line no longer uses it). Capability ClassVars gate
    provider-specific pipeline steps in ``model_service.run_model_gen_pipeline`` —
    orchestration checks them before calling the optional rig or multiview methods.
    """

    provider_name: str = ""
    SUPPORTS_RIGGING: ClassVar[bool] = False
    SUPPORTS_MULTIVIEW: ClassVar[bool] = False
    SUPPORTS_NEGATIVE_PROMPT: ClassVar[bool] = False

    @abstractmethod
    async def poll(self, job: Model3DJob) -> Model3DPollResult:
        """Poll the generation status of a submitted job."""

    @abstractmethod
    async def download(self, result: Model3DPollResult, dest_dir: Path) -> Path:
        """Download the completed job's model into ``dest_dir`` and return the
        local path of a single ``.glb`` file (archives are unpacked here)."""

    async def submit_text_to_model(self, prompt: str, *, negative_prompt: str | None = None) -> Model3DJob:
        raise ImageTo3DError(f"{self.provider_name or type(self).__name__} does not support text-to-3D", provider=self.provider_name)

    async def submit_image_to_model(self, image_path: Path, *, multiview_paths: dict[str, Path] | None = None) -> Model3DJob:
        """Submit from local seed images (retained capability, unused by the
        generation line). Providers digest their own upload mechanism (file_token,
        base64, …) — callers only hand over local paths."""
        raise ImageTo3DError(f"{self.provider_name or type(self).__name__} does not support image-to-3D", provider=self.provider_name)

    async def rig_supported(self, job_id: str) -> bool:
        raise ImageTo3DError(f"{self.provider_name or type(self).__name__} does not support cloud rigging", provider=self.provider_name)

    async def start_rig(self, job_id: str, rig_type: str) -> Model3DJob:
        raise ImageTo3DError(f"{self.provider_name or type(self).__name__} does not support cloud rigging", provider=self.provider_name)
