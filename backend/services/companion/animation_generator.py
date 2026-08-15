import math
from typing import Any

from components import get_logger, safe_json_loads
from sqlalchemy.ext.asyncio import AsyncSession

from ._validators import clamp_value, parse_tags
from .personality_tagger import ChatFn

logger = get_logger(__name__)

RIG_DEFAULT_BONES: dict[str, list[str]] = {
    "biped": [
        "Hips",
        "Spine",
        "Spine1",
        "Spine2",
        "Neck",
        "Head",
        "Jaw",
        "LeftEye",
        "RightEye",
        "LeftArm",
        "LeftForeArm",
        "RightArm",
        "RightForeArm",
        "LeftUpLeg",
        "LeftLeg",
        "RightUpLeg",
        "RightLeg",
        "LeftShoulder",
        "RightShoulder",
        "LeftHand",
        "RightHand",
        "LeftFoot",
        "RightFoot",
        "LeftToeBase",
        "RightToeBase",
    ],
    "quadruped": [
        "Hips",
        "Spine",
        "Spine1",
        "Spine2",
        "Neck",
        "Head",
        "Jaw",
        "LeftFrontLeg",
        "LeftFrontKnee",
        "LeftFrontFoot",
        "RightFrontLeg",
        "RightFrontKnee",
        "RightFrontFoot",
        "LeftHindLeg",
        "LeftHindKnee",
        "LeftHindFoot",
        "RightHindLeg",
        "RightHindKnee",
        "RightHindFoot",
        "Tail",
        "Tail1",
        "Tail2",
    ],
    "avian": [
        "Hips",
        "Spine",
        "Spine1",
        "Neck",
        "Head",
        "Jaw",
        "LeftWing1",
        "LeftWing2",
        "LeftWing3",
        "RightWing1",
        "RightWing2",
        "RightWing3",
        "LeftLeg",
        "LeftFoot",
        "RightLeg",
        "RightFoot",
        "Tail1",
        "Tail2",
        "Tail3",
    ],
    "serpentine": [
        "Hips",
        "Spine",
        "Spine1",
        "Spine2",
        "Spine3",
        "Spine4",
        "Spine5",
        "Spine6",
        "Spine7",
        "Spine8",
        "Spine9",
        "Neck",
        "Head",
        "Jaw",
        "Tail1",
        "Tail2",
        "Tail3",
        "Tail4",
        "Tail5",
    ],
    "aquatic": ["Hips", "Spine", "Spine1", "Spine2", "Neck", "Head", "Jaw", "TopFin", "BottomFin", "LeftFin", "RightFin", "Tail1", "Tail2", "Tail3", "Tail4"],
    "hexapod": [
        "Hips",
        "Spine",
        "Spine1",
        "Spine2",
        "Neck",
        "Head",
        "Jaw",
        "LeftAntenna",
        "RightAntenna",
        "LeftFrontLeg",
        "LeftFrontKnee",
        "LeftFrontFoot",
        "LeftMidLeg",
        "LeftMidKnee",
        "LeftMidFoot",
        "LeftHindLeg",
        "LeftHindKnee",
        "LeftHindFoot",
        "RightFrontLeg",
        "RightFrontKnee",
        "RightFrontFoot",
        "RightMidLeg",
        "RightMidKnee",
        "RightMidFoot",
        "RightHindLeg",
        "RightHindKnee",
        "RightHindFoot",
        "Tail1",
        "Tail2",
    ],
    "octopod": [
        "Hips",
        "Spine",
        "Spine1",
        "Spine2",
        "Neck",
        "Head",
        "Jaw",
        "LeftFrontLeg",
        "LeftMidFrontLeg",
        "LeftMidBackLeg",
        "LeftBackLeg",
        "RightFrontLeg",
        "RightMidFrontLeg",
        "RightMidBackLeg",
        "RightBackLeg",
        "Tail1",
    ],
}


def get_rig_bones(rig_type: str | None) -> list[str]:
    normalized = (rig_type or "").strip().lower()
    return list(RIG_DEFAULT_BONES.get(normalized, RIG_DEFAULT_BONES["biped"]))


_SYSTEM_PROMPT = (
    "你是一个 3D 骨骼动画关键帧生成专家。请根据给出的骨骼列表、生物物种、骨骼类型以及专属性格/行为标签，"
    "为角色生成生动、符合其性格特质的 3D 动画 Clip 定义（JSON 格式）。\n"
    "输出要求：\n"
    "输出一个 JSON 对象数组，每个对象代表一个动画 Clip，格式如下：\n"
    "{\n"
    '  "name": "poke_seductive",\n'
    '  "duration": 2.0,\n'
    '  "loop": false,\n'
    '  "category": "interaction",\n'
    '  "tags": ["妖娆", "妩媚"],\n'
    '  "tracks": {\n'
    '    "Head": [{"t": 0, "r": [0, 0, 0]}, {"t": 1.0, "r": [0.1, 0.2, 0]}, {"t": 2.0, "r": [0, 0, 0]}],\n'
    '    "Spine": [{"t": 0, "r": [0, 0, 0]}, {"t": 1.0, "r": [0.05, -0.1, 0]}, {"t": 2.0, "r": [0, 0, 0]}]\n'
    "  }\n"
    "}\n"
    "规则与物理约束：\n"
    "1. tracks 的 key 必须严格来自给定的可用骨骼名列表；\n"
    "2. 旋转 r 为欧拉角 [x, y, z]（单位弧度），通常范围在 [-1.5, 1.5] 之间，严禁过激形变；\n"
    "3. loop 为 true 时，首帧 (t=0) 与尾帧 (t=duration) 的旋转值必须保持一致；\n"
    "4. 每个 clip 必须带有 tags 字段（包含对应的性格标签）；\n"
    "5. 必须输出纯 JSON 数组，不要任何 markdown 说明。"
)


def validate_and_sanitize_clip(clip_data: dict[str, Any], allowed_bones: set[str] | None = None) -> dict[str, Any] | None:
    if not isinstance(clip_data, dict):
        return None

    name = str(clip_data.get("name", "")).strip()
    if not name:
        return None

    try:
        duration = float(clip_data.get("duration", 2.0))
        if duration <= 0:
            duration = 2.0
    except (TypeError, ValueError):
        duration = 2.0

    loop = bool(clip_data.get("loop", False))
    category = str(clip_data.get("category", "interaction")).strip() or "interaction"

    tags = parse_tags(clip_data.get("tags"))

    raw_tracks = clip_data.get("tracks")
    if not isinstance(raw_tracks, dict) or not raw_tracks:
        return None

    sanitized_tracks: dict[str, list[dict[str, Any]]] = {}
    for bone, kfs in raw_tracks.items():
        bone_name = str(bone).strip()
        if allowed_bones and bone_name not in allowed_bones:
            continue
        if not isinstance(kfs, list) or len(kfs) < 2:
            continue

        clean_kfs: list[dict[str, Any]] = []
        for item in kfs:
            if not isinstance(item, dict):
                continue
            try:
                t = float(item.get("t", 0))
                r = item.get("r")
                if isinstance(r, (list, tuple)) and len(r) == 3:
                    rx, ry, rz = clamp_value(r[0], -math.pi, math.pi), clamp_value(r[1], -math.pi, math.pi), clamp_value(r[2], -math.pi, math.pi)
                    clean_kfs.append({"t": round(t, 3), "r": [rx, ry, rz]})
            except (TypeError, ValueError):
                continue

        if len(clean_kfs) >= 2:
            clean_kfs.sort(key=lambda k: k["t"])
            # loop 平滑闭合修复：保证首尾旋转一致且尾帧时间严格等于 duration
            if loop:
                first_r = clean_kfs[0]["r"]
                dur_rounded = round(duration, 3)
                if clean_kfs[-1]["t"] < dur_rounded:
                    clean_kfs.append({"t": dur_rounded, "r": [first_r[0], first_r[1], first_r[2]]})
                else:
                    clean_kfs[-1]["t"] = dur_rounded
                    clean_kfs[-1]["r"] = [first_r[0], first_r[1], first_r[2]]
            sanitized_tracks[bone_name] = clean_kfs

    if not sanitized_tracks:
        return None

    return {"name": name, "duration": round(duration, 3), "loop": loop, "category": category, "tags": tags, "tracks": sanitized_tracks}


async def generate_animation_clips(
    chat: ChatFn,
    rig_type: str,
    bone_list: list[str] | None = None,
    personality_tags: list[str] | None = None,
    species: str = "人类",
    categories: list[str] | None = None,
    *,
    user_id: int | None = None,
    db: AsyncSession | None = None,
) -> list[dict]:
    if not personality_tags:
        return []

    effective_bones = bone_list if bone_list else get_rig_bones(rig_type)
    allowed_bones = set(effective_bones)
    cat_list = categories or ["interaction", "emotion-positive", "emotion-negative"]

    user_payload = (
        f"物种: {species}\n"
        f"骨骼类型: {rig_type}\n"
        f"可用骨骼列表: {', '.join(effective_bones[:30])}\n"
        f"目标性格标签集: {', '.join(personality_tags)}\n"
        f"目标动作分类: {', '.join(cat_list)}\n"
        f"请为上述性格标签生成 2-4 个专属动作 Clip 定义（JSON 数组）："
    )

    try:
        raw = await chat(db, user_id, _SYSTEM_PROMPT, user_payload)
        cleaned_raw = raw.strip()
        if cleaned_raw.startswith("```"):
            cleaned_raw = cleaned_raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        parsed = safe_json_loads(cleaned_raw, default=[])
        if not isinstance(parsed, list):
            return []

        results: list[dict] = []
        for item in parsed:
            sanitized = validate_and_sanitize_clip(item, allowed_bones)
            if sanitized:
                # 确保生成的 clip 至少附带一个目标 personality tag
                if not sanitized["tags"]:
                    sanitized["tags"] = personality_tags[:2]
                results.append(sanitized)

        return results
    except Exception:
        logger.warning("Failed to generate animation clips via LLM", exc_info=True)
        return []
