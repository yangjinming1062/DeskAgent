"""视觉 LLM 部件合理性校验 — 部件数 / bbox 面积 / 关键点数量基本检查。"""

from components import get_logger

from .layer_extractor import ExtractedLayer

logger = get_logger(__name__)


_MIN_LAYER_COUNT = 4
_MAX_LAYER_AREA = 0.95
_MIN_TOTAL_AREA = 0.2


def validate_layers(layers: list[ExtractedLayer]) -> tuple[bool, str]:
    """部件集合是否合理；返回 (ok, reason)；失败时上层会走兜底（程序化蛋）。"""
    if len(layers) < _MIN_LAYER_COUNT:
        return False, f"layer count {len(layers)} < {_MIN_LAYER_COUNT}"

    total_area = 0.0

    for layer in layers:
        x1, y1, x2, y2 = layer.bbox
        area = max(0.0, x2 - x1) * max(0.0, y2 - y1)

        if area > _MAX_LAYER_AREA:
            return False, f"layer {layer.name} bbox area {area:.2f} too large"

        total_area += area

    if total_area < _MIN_TOTAL_AREA:
        return False, f"total layer area {total_area:.2f} < {_MIN_TOTAL_AREA}"

    return True, "ok"
