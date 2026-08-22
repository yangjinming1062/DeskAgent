import io
import threading
from typing import Any

import numpy as np
import rembg
from PIL import Image
from scipy import ndimage

from .config import SETTINGS
from .logger import get_logger

logger = get_logger(__name__)

_MIN_OPAQUE_FRAC = 0.15
_MAX_SEMI_FRAC = 0.08
_CORE_DIST = 25.0
_SOFT_DIST = 110.0


def has_real_transparency(data: bytes) -> bool:
    """判断 PNG 是否带有真实透明通道。"""
    try:
        img = Image.open(io.BytesIO(data))
        if img.mode not in ("RGBA", "LA") and not (img.mode == "P" and "transparency" in img.info):
            return False
        alpha = np.asarray(img.convert("RGBA").getchannel("A"), dtype=np.uint8)
        return bool(
            alpha.min() <= 8 and np.count_nonzero(alpha == 255) >= _MIN_OPAQUE_FRAC * alpha.size and np.count_nonzero((alpha > 0) & (alpha < 255)) <= _MAX_SEMI_FRAC * alpha.size,
        )
    except OSError:
        return False


def vectorized_matting(data: bytes) -> bytes:
    """纯 CPU 向量化白底形态学抠图：用于无 ONNX 权重或模型异常时的毫秒级快速兜底。"""
    img = Image.open(io.BytesIO(data)).convert("RGB")
    rgb = np.asarray(img, dtype=np.float32)
    h, w = rgb.shape[:2]
    pad = max(1, min(8, h // 2, w // 2))
    ring = np.concatenate(
        [
            rgb[:pad].reshape(-1, 3),
            rgb[h - pad :].reshape(-1, 3),
            rgb[pad : h - pad, :pad].reshape(-1, 3),
            rgb[pad : h - pad, w - pad :].reshape(-1, 3),
        ],
    )
    ring_means = ring.mean(axis=1)
    thresh = np.percentile(ring_means, 70)
    bg = np.median(ring[ring_means >= thresh], axis=0) if np.max(ring_means) >= 180 else np.median(ring, axis=0)

    dist = np.linalg.norm(rgb - bg, axis=2)
    soft = dist <= _SOFT_DIST
    labeled, num_features = ndimage.label(soft)
    if num_features > 0:
        border_labels = np.unique(
            np.concatenate(
                [
                    labeled[0, :],
                    labeled[-1, :],
                    labeled[:, 0],
                    labeled[:, -1],
                ],
            ),
        )
        border_labels = border_labels[border_labels > 0]
        bg_connected = np.isin(labeled, border_labels)
    else:
        bg_connected = soft

    t = np.clip((dist - _CORE_DIST) / (_SOFT_DIST - _CORE_DIST), 0.0, 1.0)
    alpha = np.where(bg_connected, np.round(255.0 * t * t), 255.0).astype(np.uint8)
    alpha[alpha < 16] = 0

    a = np.maximum(alpha.astype(np.float32) / 255.0, 1.0 / 255.0)[..., None]
    unmixed = np.clip((rgb - (1.0 - a) * bg) / a, 0.0, 255.0)
    edge = ((alpha > 0) & (alpha < 255))[..., None]
    out = np.where(edge, unmixed, rgb).astype(np.uint8)

    buf = io.BytesIO()
    Image.fromarray(np.dstack([out, alpha]), "RGBA").save(buf, format="PNG")
    return buf.getvalue()


class MattingEngine:
    """背景抠图引擎单例：优先使用 ONNX 深度学习模型分割发丝与细节，异常时无缝降级至向量化形态学白底抠图。"""

    _instance: "MattingEngine | None" = None
    _lock = threading.Lock()

    def __init__(self, model_name: str | None = None) -> None:
        self.model_name = model_name or SETTINGS.matting_model
        self._session: Any = None
        self._session_initialized = False
        self._init_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "MattingEngine":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def _ensure_session(self) -> Any:
        if not self._session_initialized:
            with self._init_lock:
                if not self._session_initialized:
                    try:
                        self._session = rembg.new_session(self.model_name)
                    except Exception as exc:
                        logger.warning("Failed to initialize rembg session, using vectorized fallback", extra={"error": str(exc)})
                        self._session = None
                    self._session_initialized = True
        return self._session

    def remove_background(self, data: bytes) -> bytes:
        session = self._ensure_session()
        if session is not None:
            try:
                result = rembg.remove(data, session=session)
                if isinstance(result, bytes) and has_real_transparency(result):
                    return result
            except Exception as exc:
                logger.info("AI matting inference failed, falling back to vectorized matting", extra={"error": str(exc)})

        return vectorized_matting(data)


def remove_background(data: bytes) -> bytes:
    return MattingEngine.get_instance().remove_background(data)


def warmup_matting_engine() -> bool:
    """应用启动时预热并检查/下载 ONNX 抠图模型文件；失败时记录警告并平滑降级至向量化形态学。"""
    engine = MattingEngine.get_instance()
    session = engine._ensure_session()
    if session is not None:
        logger.info("Matting engine warmed up successfully", extra={"model": engine.model_name})
        return True
    logger.warning("Matting engine session unavailable, using vectorized fallback", extra={"model": engine.model_name})
    return False
