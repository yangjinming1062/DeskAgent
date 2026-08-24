"""视觉 LLM 关键点估计 — 输出 22 个姿态关键点，含解剖学约束平滑。"""

from components import get_logger, safe_json_loads

from services.llm import ProviderConfig

from .prompts import POSE_ESTIMATION_SYSTEM_PROMPT, POSE_ESTIMATION_USER_TEMPLATE
from .region_detector import _strip_fence, call_vision_llm

logger = get_logger(__name__)


# 躯干核心为硬门槛；面部点（鼻/眼）是视觉模型对小脸孔最常判 null 的部位，
# 缺失时由 head 锚点按比例合成（见 sanitize_keypoints），不作为门槛。
_REQUIRED_KEYPOINT_NAMES: tuple[str, ...] = (
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
    """至少有 5 个躯干核心关键点（肩 / 髋 / 颈）才算可用。"""
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

    # 缺关键点回退到部件几何中心。
    if layer_centers:
        for fallback_name, layer_name in (
            ("head_top", "front_hair"),
            ("hair_back_root", "back_hair"),
            ("neck", "body_main"),
        ):
            if fallback_name not in out and layer_name in layer_centers:
                out[fallback_name] = layer_centers[layer_name]

    # 中线：head 锚点优先双眼中点，其次头顶-颈中点，最后躯干中心。
    if get("left_eye") and get("right_eye"):
        l_eye, r_eye = get("left_eye"), get("right_eye")
        out["head"] = ((l_eye[0] + r_eye[0]) / 2, (l_eye[1] + r_eye[1]) / 2)
    elif all(name in out for name in ("head_top", "neck")):
        out["head"] = ((out["head_top"][0] + out["neck"][0]) / 2, (out["head_top"][1] + out["neck"][1]) / 2)
    elif layer_centers and "body_main" in layer_centers:
        out["head"] = layer_centers["body_main"]

    # 面部点合成：视觉模型对小脸孔常把眼/鼻判 null，按典型面部比例从 head 锚点补齐
    # （setdefault 不覆盖真实值）——否则骨骼层 eye/mouth pivot 落到画布中心兜底。
    if "head" in out:
        hx, hy = out["head"]
        for name, xy in (
            ("left_eye", (hx - 0.035, hy)),
            ("right_eye", (hx + 0.035, hy)),
            ("nose", (hx, hy + 0.035)),
        ):
            out.setdefault(name, xy)

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

    # 手部无独立图层可回退：缺失时取手腕下方一掌距离（骨骼 hand pivot 兜底同此规则）。
    for hand, wrist in (("left_hand", "left_wrist"), ("right_hand", "right_wrist")):
        if hand not in out and wrist in out:
            out[hand] = (out[wrist][0], min(out[wrist][1] + 0.05, 0.98))

    return out


async def estimate_pose(
    chain: list[ProviderConfig],
    user_id: int | None,
    data_uri: str,
    *,
    layer_centers: dict[str, tuple[float, float]] | None = None,
) -> dict[str, tuple[float, float]]:
    """调用视觉 LLM 输出 22 关键点；返回经解剖学约束平滑后的字典。"""
    if not chain:
        logger.warning(
            "vision LLM chain is empty; pose estimation skipped",
            extra={"user_id": user_id},
        )
        return {}

    try:
        raw = await call_vision_llm(chain, user_id, POSE_ESTIMATION_SYSTEM_PROMPT, POSE_ESTIMATION_USER_TEMPLATE, data_uri)
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
