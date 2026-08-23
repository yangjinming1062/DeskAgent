"""视觉 LLM 区域识别 — 输出 6 个核心物理层 bbox。"""

from dataclasses import dataclass

from components import get_logger, safe_json_loads
from sqlalchemy.ext.asyncio import AsyncSession

from services.llm import execute_with_fallback, resolve_vision_chain

from .prompts import REGION_DETECTION_SYSTEM_PROMPT, REGION_DETECTION_USER_TEMPLATE

logger = get_logger(__name__)


_REQUIRED_LAYER_NAMES: tuple[str, ...] = (
    "back_hair",
    "body_main",
    "arm_L",
    "arm_R",
    "front_hair",
)


@dataclass(frozen=True)
class DetectedLayer:
    name: str
    bbox: tuple[float, float, float, float]
    z_order: int
    occluded_by: tuple[str, ...]

    def to_dict(self) -> dict:
        return {"name": self.name, "bbox": list(self.bbox), "z_order": self.z_order, "occluded_by": list(self.occluded_by)}


def _strip_fence(raw: str) -> str:
    cleaned = raw.strip()

    if cleaned.startswith("```"):
        first_nl = cleaned.find("\n")

        if first_nl != -1 and cleaned.endswith("```") and len(cleaned) > first_nl + 3:
            cleaned = cleaned[first_nl + 1 : -3].strip()

    return cleaned


def _validate_bbox(bbox: list) -> tuple[float, float, float, float] | None:
    if not isinstance(bbox, list) or len(bbox) != 4:
        return None

    try:
        x1, y1, x2, y2 = (float(v) for v in bbox)
    except (TypeError, ValueError):
        return None

    if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
        return None

    return (x1, y1, x2, y2)


def parse_layers_payload(raw: str) -> list[DetectedLayer]:
    """解析视觉 LLM 输出；失败时返回空列表，由调用方决定是否兜底。"""
    text = _strip_fence(raw)
    payload = safe_json_loads(text, default=None)

    if not isinstance(payload, dict):
        return []

    layers_raw = payload.get("layers")

    if not isinstance(layers_raw, list):
        return []

    layers: list[DetectedLayer] = []

    for entry in layers_raw:
        if not isinstance(entry, dict):
            continue

        name = entry.get("name")

        if not isinstance(name, str) or not name:
            continue

        bbox = _validate_bbox(entry.get("bbox"))

        if bbox is None:
            continue

        try:
            z_order = int(entry.get("z_order", 0))
        except (TypeError, ValueError):
            z_order = 0

        occluded_raw = entry.get("occluded_by", [])

        if not isinstance(occluded_raw, list):
            occluded_raw = []

        occluded = tuple(str(o) for o in occluded_raw if isinstance(o, str))

        layers.append(DetectedLayer(name=name, bbox=bbox, z_order=z_order, occluded_by=occluded))

    return layers


def has_minimum_layers(layers: list[DetectedLayer]) -> bool:
    """至少包含 5 个核心图层（clothing 可选）；用来拒绝明显错配的结果。"""
    names = {layer.name for layer in layers}
    return all(req in names for req in _REQUIRED_LAYER_NAMES)


async def detect_regions(db: AsyncSession | None, user_id: int | None, data_uri: str) -> list[DetectedLayer]:
    """调用视觉 LLM 识别 6 部件 bbox；视觉链为空时直接返回空列表。"""
    chain = await resolve_vision_chain(db, user_id)

    if not chain:
        logger.warning("vision LLM chain is empty; region detection skipped", extra={"user_id": user_id})
        return []

    system_prompt = REGION_DETECTION_SYSTEM_PROMPT
    user_payload = REGION_DETECTION_USER_TEMPLATE.format(data_uri=data_uri)

    try:
        raw = await execute_with_fallback(
            chain,
            system_prompt=system_prompt,
            user_payload=user_payload,
            db=db,
            user_id=user_id,
        )
    except Exception as exc:
        logger.warning("vision LLM region detection failed", extra={"user_id": user_id, "error": str(exc)})
        return []

    layers = parse_layers_payload(raw)

    if not has_minimum_layers(layers):
        logger.warning(
            "vision LLM returned insufficient layers",
            extra={"user_id": user_id, "count": len(layers), "raw_len": len(raw)},
        )
        return []

    logger.info("vision LLM detected regions", extra={"user_id": user_id, "count": len(layers)})
    return layers
