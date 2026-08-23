"""视觉 LLM 关键点估计 — 输出 20 个姿态关键点，含解剖学约束平滑。"""

from components import get_logger, safe_json_loads
from sqlalchemy.ext.asyncio import AsyncSession

from services.llm import execute_with_fallback, resolve_vision_chain

from .prompts import POSE_ESTIMATION_SYSTEM_PROMPT, POSE_ESTIMATION_USER_TEMPLATE
from .region_detector import _strip_fence

logger = get_logger(__name__)


_REQUIRED_KEYPOINT_NAMES: tuple[str, ...] = (
    "nose",
    "left_eye",
    "right_eye",
    "left_shoulder",
    "right_shoulder",
    "left_hip",
    "right_hip",
    "neck",
)


def _parse_xy(entry) -> tuple[float, float] | None:
    if not isinstance(entry, list) or len(entry) != 2:
        return None

    try:
        x, y = (float(v) for v in entry)
    except (TypeError, ValueError):
        return None

    if not (0 <= x <= 1 and 0 <= y <= 1):
        return None

    return (x, y)


def parse_keypoints_payload(raw: str) -> dict[str, tuple[float, float]]:
    """解析视觉 LLM 输出的关键点；缺失字段不出现，由 sanitize_keypoints 兜底。"""
    text = _strip_fence(raw)
    payload = safe_json_loads(text, default=None)

    if not isinstance(payload, dict):
        return {}

    kp_raw = payload.get("keypoints")

    if not isinstance(kp_raw, dict):
        return {}

    parsed: dict[str, tuple[float, float]] = {}

    for name, value in kp_raw.items():
        if not isinstance(name, str) or value is None:
            continue

        xy = _parse_xy(value)

        if xy is None:
            continue

        parsed[name] = xy

    return parsed


def has_minimum_keypoints(kp: dict[str, tuple[float, float]]) -> bool:
    """至少有 8 个核心关键点（含眼 / 鼻 / 肩 / 髋 / 颈）才算可用。"""
    return all(name in kp for name in _REQUIRED_KEYPOINT_NAMES)


def sanitize_keypoints(
    kp: dict[str, tuple[float, float]],
    *,
    layer_centers: dict[str, tuple[float, float]] | None = None,
) -> dict[str, tuple[float, float]]:
    """解剖学约束平滑：对称性、中线、比例；缺关键点回退到部件几何中心。"""

    def get(name: str) -> tuple[float, float] | None:
        return out.get(name)

    out = {k: (max(0.0, min(1.0, float(v[0]))), max(0.0, min(1.0, float(v[1])))) for k, v in kp.items() if isinstance(v, tuple | list) and len(v) >= 2}

    # 对称性：左右肩 / 髋 / 耳 / 眼 Y 坐标差异超过 2% 取均值（消除歪肩）。
    for pair in (
        ("left_shoulder", "right_shoulder"),
        ("left_hip", "right_hip"),
        ("left_ear", "right_ear"),
        ("left_eye", "right_eye"),
    ):
        left, right = get(pair[0]), get(pair[1])

        if left is None or right is None or abs(left[1] - right[1]) <= 0.02:
            continue

        avg = (left[1] + right[1]) / 2
        left = (left[0], avg)
        right = (right[0], avg)
        out[pair[0]] = left
        out[pair[1]] = right

    # 中线：head pivot X = 双眼 X 中点；Y = 双眼 Y 中点。
    if get("left_eye") and get("right_eye"):
        l_eye, r_eye = get("left_eye"), get("right_eye")
        out["head"] = ((l_eye[0] + r_eye[0]) / 2, (l_eye[1] + r_eye[1]) / 2)
    elif layer_centers and "body_main" in layer_centers:
        out["head"] = layer_centers["body_main"]

    # 比例：head 与 neck 间距不应超过 neck 长度 1.5 倍；超过时 head 沿 neck→head_top 方向裁剪。
    if all(name in out for name in ("head", "neck", "head_top")):
        head = out["head"]
        neck = out["neck"]
        top = out["head_top"]
        dist = ((head[0] - neck[0]) ** 2 + (head[1] - neck[1]) ** 2) ** 0.5
        neck_len = ((top[0] - neck[0]) ** 2 + (top[1] - neck[1]) ** 2) ** 0.5

        if neck_len > 0 and dist > 1.5 * neck_len:
            scale = 1.5 * neck_len / dist
            out["head"] = (
                neck[0] + (head[0] - neck[0]) * scale,
                neck[1] + (head[1] - neck[1]) * scale,
            )

    # 缺关键点回退到部件几何中心。
    if layer_centers:
        for fallback_name, layer_name in (
            ("head_top", "front_hair"),
            ("hair_back_root", "back_hair"),
            ("neck", "body_main"),
        ):
            if fallback_name not in out and layer_name in layer_centers:
                out[fallback_name] = layer_centers[layer_name]

    return out


async def estimate_pose(
    db: AsyncSession | None,
    user_id: int | None,
    data_uri: str,
    *,
    layer_centers: dict[str, tuple[float, float]] | None = None,
) -> dict[str, tuple[float, float]]:
    """调用视觉 LLM 输出 20 关键点；返回经解剖学约束平滑后的字典。"""
    chain = await resolve_vision_chain(db, user_id)

    if not chain:
        logger.warning(
            "vision LLM chain is empty; pose estimation skipped",
            extra={"user_id": user_id},
        )
        return {}

    system_prompt = POSE_ESTIMATION_SYSTEM_PROMPT
    user_payload = POSE_ESTIMATION_USER_TEMPLATE.format(data_uri=data_uri)

    try:
        raw = await execute_with_fallback(
            chain,
            system_prompt=system_prompt,
            user_payload=user_payload,
            db=db,
            user_id=user_id,
        )
    except Exception as exc:
        logger.warning(
            "vision LLM pose estimation failed",
            extra={"user_id": user_id, "error": str(exc)},
        )
        return {}

    kp = parse_keypoints_payload(raw)

    if not has_minimum_keypoints(kp):
        logger.warning(
            "vision LLM returned insufficient keypoints",
            extra={"user_id": user_id, "count": len(kp), "raw_len": len(raw)},
        )
        return {}

    smoothed = sanitize_keypoints(kp, layer_centers=layer_centers)
    logger.info(
        "vision LLM estimated pose keypoints",
        extra={"user_id": user_id, "count": len(smoothed)},
    )
    return smoothed
