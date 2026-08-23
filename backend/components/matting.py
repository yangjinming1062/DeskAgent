import io
import os
import shutil
import threading
from pathlib import Path
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
_CORE_DIST = 15.0
_SOFT_DIST = 40.0


def _configure_rembg_environment() -> Path:
    """配置 rembg 本地模型检索目录，避免外部网络请求与多余的目录层级。"""
    data_dir = Path(SETTINGS.data_dir).resolve()
    os.environ["REMBG_HOME"] = str(data_dir)
    # 清理可能干扰 rembg_home 解析的遗留 U2NET_HOME
    if "U2NET_HOME" in os.environ and os.environ["U2NET_HOME"] != str(data_dir):
        os.environ.pop("U2NET_HOME", None)
    return data_dir


def _normalize_local_model_path(model_name: str) -> Path | None:
    """探测并自动归一化本地 ONNX 权重文件到 rembg 标准路径 models/<name>/<name>.onnx。"""
    data_dir = _configure_rembg_environment()
    target_dir = data_dir / "models" / model_name
    target_file = target_dir / f"{model_name}.onnx"
    if target_file.is_file():
        return target_file

    candidates = [
        data_dir / "models" / f"{model_name}.onnx",
        data_dir / "models" / "models" / model_name / f"{model_name}.onnx",
        data_dir / f"{model_name}.onnx",
    ]
    for cand in candidates:
        if cand.is_file():
            try:
                target_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(cand), str(target_file))
                logger.info("Normalized local matting model path", extra={"src": str(cand), "dst": str(target_file)})
                # 清理多余空目录
                parent = cand.parent
                if parent != data_dir and parent != data_dir / "models" and not any(parent.iterdir()):
                    parent.rmdir()
                return target_file
            except Exception as exc:
                logger.warning("Failed to normalize local model path", extra={"src": str(cand), "error": str(exc)})
                return cand

    return None


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
    # 肖像场景背景主要从顶部及两侧上角采样，避免把触碰下边缘/侧边的人体皮肤算入背景
    top_region = np.concatenate(
        [
            rgb[:pad].reshape(-1, 3),
            rgb[: pad * 4, : pad * 4].reshape(-1, 3),
            rgb[: pad * 4, w - pad * 4 :].reshape(-1, 3),
        ],
    )
    top_means = top_region.mean(axis=1)
    thresh = np.percentile(top_means, 70)
    bg = np.median(top_region[top_means >= thresh], axis=0) if np.max(top_means) >= 180 else np.median(top_region, axis=0)

    dist = np.linalg.norm(rgb - bg, axis=2)
    soft = dist <= _SOFT_DIST
    labeled, num_features = ndimage.label(soft)
    if num_features > 0:
        # 背景种子仅从顶部边缘及上方两角泛洪，禁止以身体接触的底部与两侧下边缘作为背景种子
        top_labels = np.unique(
            np.concatenate(
                [
                    labeled[0, :],
                    labeled[:pad, 0],
                    labeled[:pad, -1],
                ],
            ),
        )
        top_labels = top_labels[top_labels > 0]
        bg_connected = np.isin(labeled, top_labels)
    else:
        bg_connected = soft

    # 前景孔洞闭合：人物躯干/手臂内部的高光即使色差小也被前景完全封闭，禁止挖空
    fg_mask = ~bg_connected
    fg_filled = ndimage.binary_fill_holes(fg_mask)
    bg_connected = ~fg_filled

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
        self._init_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "MattingEngine":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def _ensure_session(self) -> Any:
        if self._session is not None:
            return self._session
        with self._init_lock:
            if self._session is not None:
                return self._session
            try:
                _configure_rembg_environment()
                _normalize_local_model_path(self.model_name)
                self._session = rembg.new_session(self.model_name)
                logger.info("Matting engine session initialized", extra={"model": self.model_name})
            except Exception as exc:
                logger.warning("Failed to initialize rembg session, using vectorized fallback", extra={"model": self.model_name, "error": str(exc)})
                self._session = None
        return self._session

    def remove_background(self, data: bytes) -> bytes:
        session = self._ensure_session()
        if session is not None:
            try:
                result = rembg.remove(data, session=session, post_process_mask=True)
                if isinstance(result, bytes) and has_real_transparency(result):
                    return result
            except Exception as exc:
                logger.info("AI matting inference failed, falling back to vectorized matting", extra={"error": str(exc)})

        return vectorized_matting(data)


def remove_background(data: bytes) -> bytes:
    return MattingEngine.get_instance().remove_background(data)


def warmup_matting_engine() -> bool:
    """应用启动时预热并检查/加载 ONNX 抠图模型文件；失败时记录警告并平滑降级至向量化形态学。"""
    engine = MattingEngine.get_instance()
    session = engine._ensure_session()
    if session is not None:
        logger.info("Matting engine warmed up successfully", extra={"model": engine.model_name})
        return True
    logger.warning("Matting engine session unavailable, using vectorized fallback", extra={"model": engine.model_name})
    return False
