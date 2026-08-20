from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Literal


@dataclass(frozen=True)
class Model3DJob:
    job_id: str


@dataclass(frozen=True)
class Model3DAsset:
    kind: str
    url: str
    preview_image_url: str | None = None


@dataclass(frozen=True)
class Model3DPollResult:
    status: Literal["queued", "in_progress", "completed", "failed"]
    # 供应商自身的 0-100 进度信号；0 表示未知（编排侧按已用时间插值）。
    progress: int = 0
    assets: tuple[Model3DAsset, ...] = ()
    error: str | None = None


@dataclass(frozen=True)
class Model3DIntermediateAsset:
    """已下载到本进程内存的链中间产物：上传阶段不落盘，链结束后唯一终产物落盘。"""

    glb_bytes: bytes
    source_kind: str  # "raw" | "rigged" | "animated"
    provider_task_id: str
    download_urls: tuple[Model3DAsset, ...] = ()


class ImageTo3DError(Exception):
    """图生 3D 服务及供应商的基础异常。"""

    def __init__(self, message: str, *, status_code: int | None = None, body: dict | None = None, provider: str = "", model: str = "") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body or {}
        self.provider = provider
        self.model = model


class ImageTo3DProvider(ABC):
    """图生 3D 供应商抽象基类；能力 ClassVars 控制 ``pipeline.run_capability_chain`` 中的供应商专属流水线步骤，编排侧在调用可选的 rig / animate_bind / multiview 方法前先检查它们。

    链拓扑:submit → poll → download → (可选) cloud_rig → (可选) cloud_animate_bind,以 ``task_id`` 串联,云端产物即终产物。
    """

    provider_name: str = ""
    SUPPORTS_RIGGING: ClassVar[bool] = False
    SUPPORTS_MULTIVIEW: ClassVar[bool] = False
    SUPPORTS_NEGATIVE_PROMPT: ClassVar[bool] = False
    SUPPORTS_ANIMATE_BIND: ClassVar[bool] = False

    @abstractmethod
    async def poll(self, job: Model3DJob) -> Model3DPollResult:
        """轮询已提交任务的生成状态。"""

    @abstractmethod
    async def download(self, result: Model3DPollResult, dest_dir: Path) -> Path:
        """把已完成任务的模型下载到 ``dest_dir``，返回单个 ``.glb`` 本地路径（压缩包在此处解压）。"""

    async def submit_image_to_model(self, image_path: Path, *, multiview_paths: dict[str, Path] | None = None) -> Model3DJob:
        """从本地种子图提交；供应商自行处理上传机制（file_token、base64 等），调用方只传本地路径。"""
        raise ImageTo3DError(f"{self.provider_name or type(self).__name__} does not support image-to-3D", provider=self.provider_name)

    async def rig_supported(self, job_id: str) -> bool:
        raise ImageTo3DError(f"{self.provider_name or type(self).__name__} does not support cloud rigging", provider=self.provider_name)

    async def start_rig(self, job_id: str, rig_type: str) -> Model3DJob:
        raise ImageTo3DError(f"{self.provider_name or type(self).__name__} does not support cloud rigging", provider=self.provider_name)

    async def start_animate_bind(self, job_id: str) -> Model3DJob:
        raise ImageTo3DError(f"{self.provider_name or type(self).__name__} does not support cloud animate-bind", provider=self.provider_name)
