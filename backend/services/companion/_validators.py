"""伴侣资产生成中 LLM 输出校验器的共用辅助函数。"""

from typing import Any


def clamp_value(v: Any, min_v: float, max_v: float) -> float:
    """将 v 截断到 [min_v, max_v]；非数值时返回 max_v 交由调用方决定取舍。"""
    try:
        return max(min_v, min(max_v, float(v)))
    except (TypeError, ValueError):
        return max_v


def parse_tags(raw: Any) -> list[str]:
    """将 LLM 输出的 tags 字段归一化为字符串列表，过滤空值。"""
    if not isinstance(raw, list):
        return []
    return [str(t).strip() for t in raw if str(t).strip()]
