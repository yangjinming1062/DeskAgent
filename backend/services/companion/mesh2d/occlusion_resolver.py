"""遮挡关系处理 — CPU 像素操作补全被遮挡区域（无 GPU Inpainting）。"""

import io

import numpy as np
from components import get_logger
from PIL import Image
from scipy import ndimage

from .layer_extractor import ExtractedLayer

logger = get_logger(__name__)


def _bytes_to_rgba(data: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(data)).convert("RGBA"))


def _rgba_to_bytes(arr: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(arr, mode="RGBA").save(buf, format="PNG")
    return buf.getvalue()


def _grow_alpha(alpha: np.ndarray, radius: int = 3) -> np.ndarray:
    """alpha 二值化后做形态学膨胀，避免边缘出现锯齿漏出下层。"""
    mask = alpha > 16
    grown = ndimage.binary_dilation(mask, iterations=radius)
    return np.where(grown, np.maximum(alpha, 96), alpha).astype(np.uint8)


def fill_occlusion(layers: list[ExtractedLayer]) -> list[ExtractedLayer]:
    """对被遮挡部件做边缘外扩填充；下层部件（z_order 较低）若与遮挡层 bbox 重叠则补全 alpha。"""
    if not layers:
        return layers

    fixed: list[ExtractedLayer] = []

    for layer in sorted(layers, key=lambda x: x.z_order):
        arr = _bytes_to_rgba(layer.png_bytes)
        out_arr = arr.copy()
        out_arr[:, :, 3] = _grow_alpha(arr[:, :, 3], radius=2)
        fixed.append(
            ExtractedLayer(
                name=layer.name,
                z_order=layer.z_order,
                occluded_by=layer.occluded_by,
                bbox=layer.bbox,
                png_bytes=_rgba_to_bytes(out_arr),
                pixel_size=layer.pixel_size,
                pixel_bbox=layer.pixel_bbox,
            ),
        )

    logger.info("occlusion fill done", extra={"count": len(fixed)})
    return fixed
