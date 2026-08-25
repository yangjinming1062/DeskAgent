"""CPU 抠图部件提取 — 两段式：先抠全图得到透明人物，再按 bbox 裁切部件。"""

import io
from dataclasses import dataclass, replace

import numpy as np
from components import get_logger, remove_background
from PIL import Image
from scipy import ndimage

from .region_detector import DetectedLayer

logger = get_logger(__name__)

# alpha 岛小于 64px 或不足最大岛 1% 判为抠图碎片丢弃；收紧内容框时四周留 4px 余量。
_MIN_ISLAND_PX = 64
_ISLAND_RATIO = 0.01
_TIGHTEN_PAD_PX = 4
# 归属让位时下层在上层边缘下保留的 underlap 宽度，静止叠放不露缝。
_UNDERLAP_PX = 2


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
    """对整图做一次抠图（rembg ONNX，权重缺失或推理异常时内部降级白底形态学），返回 (H, W, 4) RGBA 或 None。"""
    h, w = img_rgb.shape[:2]
    rgb_buf = io.BytesIO()
    Image.fromarray(img_rgb, mode="RGB").save(rgb_buf, format="PNG")

    try:
        out = remove_background(rgb_buf.getvalue())
    except Exception as exc:
        logger.warning("fullbody matting failed", extra={"error": str(exc)})
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


def _clean_alpha(alpha: np.ndarray) -> np.ndarray:
    """抠图后处理：丢小碎片岛、填内部孔洞、边缘 1px 羽化——破碎观感主要来自前两者。"""
    solid = alpha > 32
    labeled, n = ndimage.label(solid)
    if n > 1:
        sizes = ndimage.sum(solid, labeled, range(1, n + 1))
        keep = np.flatnonzero(sizes >= max(sizes.max() * _ISLAND_RATIO, _MIN_ISLAND_PX)) + 1
        solid &= np.isin(labeled, keep)
    solid = ndimage.binary_fill_holes(solid)
    soft = ndimage.gaussian_filter(solid.astype(np.float32), 1.0)
    return np.maximum(alpha * solid, soft * 255).astype(np.uint8)


def extract_layers(
    fullbody_bytes: bytes,
    regions: list[DetectedLayer],
) -> list[ExtractedLayer]:
    """按 bbox 从已抠透明背景的全图上裁切部件 PNG（带 alpha），清理噪声后收紧到内容框。"""
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
                out = remove_background(crop_buf.getvalue())
                region = np.asarray(Image.open(io.BytesIO(out)).convert("RGBA")).copy()
            except Exception as exc:
                logger.warning(
                    "bbox-local matting failed; skip layer",
                    extra={"layer_name": layer.name, "error": str(exc)},
                )
                continue

            if region.shape[2] != 4:
                continue

        alpha = _clean_alpha(region[:, :, 3])

        if _alpha_dominant_ratio(alpha) < 0.05:
            logger.info(
                "skip layer: alpha too sparse",
                extra={"layer_name": layer.name},
            )
            continue

        # VLM bbox 常带大片空白：plane 尺寸虚大会让蒙皮权重的距离计算错位，收紧到实际内容。
        ys, xs = np.where(alpha > 32)
        tx1 = max(0, int(xs.min()) - _TIGHTEN_PAD_PX)
        ty1 = max(0, int(ys.min()) - _TIGHTEN_PAD_PX)
        tx2 = min(region.shape[1], int(xs.max()) + 1 + _TIGHTEN_PAD_PX)
        ty2 = min(region.shape[0], int(ys.max()) + 1 + _TIGHTEN_PAD_PX)
        region[:, :, 3] = alpha
        region = region[ty1:ty2, tx1:tx2]

        px1, py1 = x1 + tx1, y1 + ty1
        tight_bbox_px = (px1, py1, px1 + region.shape[1], py1 + region.shape[0])

        extracted.append(
            ExtractedLayer(
                name=layer.name,
                z_order=layer.z_order,
                occluded_by=layer.occluded_by,
                bbox=(px1 / w, py1 / h, tight_bbox_px[2] / w, tight_bbox_px[3] / h),
                png_bytes=_rgba_to_bytes(region),
                pixel_size=(region.shape[1], region.shape[0]),
                pixel_bbox=tight_bbox_px,
            ),
        )

    extracted = _assign_ownership(extracted, w, h)

    logger.info(
        "extracted layers",
        extra={
            "count": len(extracted),
            "regions": len(regions),
            "used_global_matte": matted is not None,
        },
    )
    return extracted


def _assign_ownership(
    extracted: list[ExtractedLayer],
    w: int,
    h: int,
) -> list[ExtractedLayer]:
    """按 z 序做像素归属：重叠区的不透明像素归最高 z 层，下层让位（留 2px underlap）。

    bbox 裁切的各层在躯干/头部互相覆盖，双绘像素在上层移动时会撕扯下层。"""
    claimed = np.zeros((h, w), bool)
    out: list[ExtractedLayer] = []

    for layer in sorted(extracted, key=lambda x: (-x.z_order, x.name)):
        arr = np.asarray(Image.open(io.BytesIO(layer.png_bytes)).convert("RGBA")).copy()
        x1, y1, x2, y2 = layer.pixel_bbox
        claim_view = claimed[y1:y2, x1:x2]

        if claim_view.any():
            keep = ~ndimage.binary_erosion(claim_view, iterations=_UNDERLAP_PX)
            arr[:, :, 3] = arr[:, :, 3] * keep

        if _alpha_dominant_ratio(arr[:, :, 3]) < 0.05:
            logger.info(
                "drop layer: fully claimed by higher z layers",
                extra={"layer_name": layer.name},
            )
            continue

        claimed[y1:y2, x1:x2] |= arr[:, :, 3] > 32
        out.append(replace(layer, png_bytes=_rgba_to_bytes(arr)))

    return out


def layer_centers(extracted: list[ExtractedLayer]) -> dict[str, tuple[float, float]]:
    """部件几何中心（归一化坐标）；sanitize_keypoints 缺关键点时回退到这里。"""
    centers: dict[str, tuple[float, float]] = {}

    for layer in extracted:
        x1, y1, x2, y2 = layer.bbox
        centers[layer.name] = ((x1 + x2) / 2, (y1 + y2) / 2)

    return centers
