"""CPU 抠图部件提取 — 两段式：先抠全图白底得到透明人物，再按 bbox 裁切部件。"""

import io
from dataclasses import dataclass

import numpy as np
from components import get_logger
from components.matting import vectorized_matting
from PIL import Image
from scipy import ndimage

from .region_detector import DetectedLayer

logger = get_logger(__name__)


@dataclass(frozen=True)
class ExtractedLayer:
    name: str
    z_order: int
    occluded_by: tuple[str, ...]
    bbox: tuple[float, float, float, float]
    png_bytes: bytes
    pixel_size: tuple[int, int]
    pixel_bbox: tuple[int, int, int, int]


def _bbox_to_pixels(
    bbox: tuple[float, float, float, float],
    width: int,
    height: int,
) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = bbox
    px1 = max(0, min(width - 1, round(x1 * width)))
    py1 = max(0, min(height - 1, round(y1 * height)))
    px2 = max(px1 + 1, min(width, round(x2 * width)))
    py2 = max(py1 + 1, min(height, round(y2 * height)))
    return (px1, py1, px2, py2)


def _rgba_to_bytes(arr: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(arr, mode="RGBA").save(buf, format="PNG")
    return buf.getvalue()


def _matte_fullbody(img_rgb: np.ndarray) -> np.ndarray | None:
    """对整图做一次白底毫秒级抠图，返回 (H, W, 4) RGBA 或 None（失败时）。"""
    h, w = img_rgb.shape[:2]
    rgb_buf = io.BytesIO()
    Image.fromarray(img_rgb, mode="RGB").save(rgb_buf, format="PNG")

    try:
        out = vectorized_matting(rgb_buf.getvalue())
    except Exception as exc:
        logger.warning("fullbody vectorized matting failed", extra={"error": str(exc)})
        return None

    try:
        arr = np.asarray(Image.open(io.BytesIO(out)).convert("RGBA"))
    except Exception as exc:
        logger.warning("matte result decode failed", extra={"error": str(exc)})
        return None

    if arr.shape[2] != 4:
        return None

    if arr.shape[0] != h or arr.shape[1] != w:
        return None

    return arr


def _alpha_dominant_ratio(alpha: np.ndarray) -> float:
    return float(np.count_nonzero(alpha > 32) / max(alpha.size, 1))


def extract_layers(
    fullbody_bytes: bytes,
    regions: list[DetectedLayer],
) -> list[ExtractedLayer]:
    """按 bbox 从已抠透明背景的全图上裁切部件 PNG（带 alpha）。"""
    img = Image.open(io.BytesIO(fullbody_bytes)).convert("RGB")
    w, h = img.size
    base_rgb = np.asarray(img, dtype=np.uint8)

    matted = _matte_fullbody(base_rgb)
    matte_ratio = _alpha_dominant_ratio(matted[:, :, 3]) if matted is not None else 0.0
    if matted is None or matte_ratio < 0.05:
        logger.warning(
            "fullbody matting produced empty alpha; falling back to bbox-local matting",
            extra={"alpha": matte_ratio},
        )
        matted = None

    extracted: list[ExtractedLayer] = []

    for layer in regions:
        bbox_px = _bbox_to_pixels(layer.bbox, w, h)
        x1, y1, x2, y2 = bbox_px

        if min(x2 - x1, y2 - y1) < 8:
            logger.info("skip layer: bbox too small", extra={"layer_name": layer.name})
            continue

        if matted is not None:
            region = matted[y1:y2, x1:x2].copy()
        else:
            crop_buf = io.BytesIO()
            Image.fromarray(base_rgb[y1:y2, x1:x2], mode="RGB").save(
                crop_buf,
                format="PNG",
            )
            try:
                out = vectorized_matting(crop_buf.getvalue())
                region = np.asarray(Image.open(io.BytesIO(out)).convert("RGBA")).copy()
            except Exception as exc:
                logger.warning(
                    "bbox-local matting failed; skip layer",
                    extra={"layer_name": layer.name, "error": str(exc)},
                )
                continue

            if region.shape[2] != 4:
                continue

        alpha = region[:, :, 3]
        alpha_ratio = _alpha_dominant_ratio(alpha)

        if alpha_ratio < 0.05:
            logger.info(
                "skip layer: alpha too sparse",
                extra={"layer_name": layer.name, "ratio": alpha_ratio},
            )
            continue

        # 形态学闭运算去毛糙（半径 1，约 1-2px），避免 Z 排序时边缘锯齿。
        closed = ndimage.binary_closing(alpha > 32, iterations=1)
        new_alpha = np.where(closed, np.maximum(alpha, 64), alpha).astype(np.uint8)
        region[:, :, 3] = new_alpha

        extracted.append(
            ExtractedLayer(
                name=layer.name,
                z_order=layer.z_order,
                occluded_by=layer.occluded_by,
                bbox=layer.bbox,
                png_bytes=_rgba_to_bytes(region),
                pixel_size=(x2 - x1, y2 - y1),
                pixel_bbox=bbox_px,
            ),
        )

    logger.info(
        "extracted layers",
        extra={
            "count": len(extracted),
            "regions": len(regions),
            "used_global_matte": matted is not None,
        },
    )
    return extracted


def layer_centers(extracted: list[ExtractedLayer]) -> dict[str, tuple[float, float]]:
    """部件几何中心（归一化坐标）；sanitize_keypoints 缺关键点时回退到这里。"""
    centers: dict[str, tuple[float, float]] = {}

    for layer in extracted:
        x1, y1, x2, y2 = layer.bbox
        centers[layer.name] = ((x1 + x2) / 2, (y1 + y2) / 2)

    return centers
