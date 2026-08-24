"""manifest.json 生成与序列化。"""

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel

from .skeleton_builder import BoneDef, MeshDef

SCHEMA_VERSION = 3  # v3: actions 由静态 pose 表升级为关键帧 tracks


@dataclass
class Manifest:
    schema: str = "spiritagent.mesh2d/3"
    version: int = SCHEMA_VERSION
    canvas: dict[str, int] = field(default_factory=lambda: {"w": 1024, "h": 1366})
    camera: dict[str, Any] = field(
        default_factory=lambda: {"type": "orthographic", "zoom": 1.0},
    )
    skeleton: dict[str, Any] = field(default_factory=dict)
    meshes: list[dict[str, Any]] = field(default_factory=list)
    animations: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "$schema": self.schema,
            "version": self.version,
            "canvas": self.canvas,
            "camera": self.camera,
            "skeleton": self.skeleton,
            "meshes": self.meshes,
            "animations": self.animations,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))


def _bone_to_dict(bone: BoneDef) -> dict[str, Any]:
    return {
        "name": bone.name,
        "pivot": [bone.pivot[0], bone.pivot[1]],
        "parent": bone.parent,
        "z_order": bone.z_order,
    }


def _mesh_to_dict(mesh: MeshDef) -> dict[str, Any]:
    return {
        "name": mesh.name,
        "texture": mesh.texture,
        "geometry_w": mesh.geometry_w,
        "geometry_h": mesh.geometry_h,
        "z_order": mesh.z_order,
        "origin": [mesh.origin[0], mesh.origin[1]],
        "bones_influences": mesh.bones_influences,
    }


# ---------------------------------------------------------------------------
# 默认动作 / locomotion / idle variants
#
# 设计要点（来自 docs/DESIGN.md §5 + mesh2d-drivers.ts）：
# - action 是关键帧 tracks：每轨固定 bone + channel + axis，keys 为 (t_ms, v) 序列，
#   t_ms 绝对毫秒、末键后保持；ease 仅 linear / ease_in_out 两档。
# - rotation 通道 v 单位弧度（消费端直接赋 .rotation，不做 degToRad）；
#   scale 通道 v 为目标倍率（1 = 静止）；position 通道 v 为像素偏移。
# - 红线：head.rotation.{x,y,z} ∈ ±0.26 rad (≈15°)，body_main ∈ ±0.30 rad (≈17°)，
#   wrist/elbow/shoulder ∈ ±π/2 (≈90°)。bone.scale.y 累加 breathing 最大 ≤1.015。
# - locomotion 不使用 hip/knee/ankle（腿在 body_main.png 内单独旋转无效），
#   用复合躯干方案：body_main 左右倾斜 + 上下 bob + shoulder_L/R 反向摆动。
# - jump 用单次脉冲（preload_ms + hold_ms + recover_ms），不是周期公式。
# ---------------------------------------------------------------------------


# 骨骼 transform 单位约束；驱动层在写入前最后做一次 clamp，兜底防越界。
_BONE_RED_LINES: dict[str, dict[str, float]] = {
    "head": {"rot_max": 0.26},
    "body_main": {"rot_max": 0.30, "scale_y_max": 1.015},
    "neck": {"rot_max": 0.30},
    "shoulder_L": {"rot_max": 1.57},  # π/2
    "shoulder_R": {"rot_max": 1.57},
    "elbow_L": {"rot_max": 1.57},
    "elbow_R": {"rot_max": 1.57},
    "wrist_L": {"rot_max": 1.57},
    "wrist_R": {"rot_max": 1.57},
}


Ease = Literal["linear", "ease_in_out"]


class ActionKeyframe(BaseModel):
    t_ms: int
    v: float
    ease: Ease = "linear"


class ActionTrack(BaseModel):
    bone: str
    channel: Literal["rotation", "scale", "position"]
    axis: Literal["x", "y", "z"]
    keys: list[ActionKeyframe]


class ActionDef(BaseModel):
    duration_ms: int
    blend_in_ms: int
    blend_out_ms: int
    loop: bool
    tracks: list[ActionTrack]


def _rot(bone: str, axis: str, keys: list[tuple[int, float]], ease: Ease = "linear") -> ActionTrack:
    return ActionTrack(bone=bone, channel="rotation", axis=axis, keys=[ActionKeyframe(t_ms=t, v=v, ease=ease) for t, v in keys])


def _scale(bone: str, axis: str, keys: list[tuple[int, float]]) -> ActionTrack:
    return ActionTrack(bone=bone, channel="scale", axis=axis, keys=[ActionKeyframe(t_ms=t, v=v) for t, v in keys])


def _act(duration_ms: int, blend_in_ms: int, blend_out_ms: int, loop: bool, *tracks: ActionTrack) -> ActionDef:
    return ActionDef(duration_ms=duration_ms, blend_in_ms=blend_in_ms, blend_out_ms=blend_out_ms, loop=loop, tracks=list(tracks))


# 单次动作（LLM 触发或交互触发）。keys 为 (t_ms, v)，rotation 单位弧度。
DEFAULT_ACTIONS: dict[str, ActionDef] = {
    "wave_right": _act(
        1800,
        250,
        350,
        False,
        _rot("shoulder_R", "z", [(0, -0.30), (250, -0.52)]),
        _rot("elbow_R", "z", [(0, -0.45), (250, -0.78)]),
        _rot("wrist_R", "z", [(0, -0.90), (300, -1.40), (650, -0.60), (1000, -1.35), (1350, -0.70), (1800, -1.20)], "ease_in_out"),
        _rot("head", "y", [(0, 0.10), (250, 0.18)]),
    ),
    "wave_left": _act(
        1800,
        250,
        350,
        False,
        _rot("shoulder_L", "z", [(0, 0.30), (250, 0.52)]),
        _rot("elbow_L", "z", [(0, 0.45), (250, 0.78)]),
        _rot("wrist_L", "z", [(0, 0.90), (300, 1.40), (650, 0.60), (1000, 1.35), (1350, 0.70), (1800, 1.20)], "ease_in_out"),
        _rot("head", "y", [(0, -0.10), (250, -0.18)]),
    ),
    "present_right": _act(
        1200,
        200,
        300,
        False,
        _rot("shoulder_R", "z", [(0, -0.15), (200, -0.26)]),
        _rot("elbow_R", "z", [(0, -0.30), (200, -0.52)]),
        _rot("wrist_R", "z", [(0, -0.45), (200, -0.79)]),
    ),
    "present_left": _act(
        1200,
        200,
        300,
        False,
        _rot("shoulder_L", "z", [(0, 0.15), (200, 0.26)]),
        _rot("elbow_L", "z", [(0, 0.30), (200, 0.52)]),
        _rot("wrist_L", "z", [(0, 0.45), (200, 0.79)]),
    ),
    "point_right": _act(
        1600,
        250,
        350,
        False,
        _rot("shoulder_R", "z", [(0, -0.40), (300, -1.20), (1200, -1.15), (1600, -0.60)], "ease_in_out"),
        _rot("elbow_R", "z", [(0, -0.25), (300, -0.30)]),
        _rot("head", "y", [(0, 0.06), (300, 0.15)]),
    ),
    "point_left": _act(
        1600,
        250,
        350,
        False,
        _rot("shoulder_L", "z", [(0, 0.40), (300, 1.20), (1200, 1.15), (1600, 0.60)], "ease_in_out"),
        _rot("elbow_L", "z", [(0, 0.25), (300, 0.30)]),
        _rot("head", "y", [(0, -0.06), (300, -0.15)]),
    ),
    "look_away_left": _act(
        1200,
        250,
        350,
        False,
        _rot("head", "y", [(0, -0.12), (250, -0.35)]),
        _rot("body_main", "y", [(0, -0.03), (250, -0.08)]),
    ),
    "look_away_right": _act(
        1200,
        250,
        350,
        False,
        _rot("head", "y", [(0, 0.12), (250, 0.35)]),
        _rot("body_main", "y", [(0, 0.03), (250, 0.08)]),
    ),
    "turn_body_left": _act(
        2000,
        300,
        400,
        False,
        _rot("body_main", "y", [(0, -0.09), (300, -0.18)]),
        _rot("head", "y", [(0, -0.05), (300, -0.10)]),
    ),
    "turn_body_right": _act(
        2000,
        300,
        400,
        False,
        _rot("body_main", "y", [(0, 0.09), (300, 0.18)]),
        _rot("head", "y", [(0, 0.05), (300, 0.10)]),
    ),
    "lean_forward": _act(
        1500,
        300,
        400,
        False,
        _rot("body_main", "x", [(0, -0.05), (300, -0.10)]),
        _rot("neck", "x", [(0, 0.04), (300, 0.08)]),
    ),
    "shy": _act(
        2000,
        300,
        500,
        False,
        _rot("head", "y", [(0, 0.12), (300, 0.25)]),
        _rot("head", "x", [(0, -0.05), (300, -0.10)]),
        _scale("front_hair", "y", [(0, 1.02), (300, 1.05)]),
    ),
    "idle_glance": _act(800, 200, 200, False, _rot("head", "y", [(0, -0.20)])),
    "petting": _act(
        2000,
        200,
        400,
        False,
        _rot("head", "z", [(0, 0.08)]),
        _rot("head", "x", [(0, -0.05)]),
        _scale("front_hair", "y", [(0, 1.03)]),
    ),
    "dizzy": _act(
        2500,
        200,
        400,
        False,
        _rot("head", "z", [(0, -0.12), (600, 0.10), (1200, -0.12), (1800, 0.08), (2500, -0.04)], "ease_in_out"),
        _rot("head", "x", [(0, 0.08)]),
        _rot("body_main", "z", [(0, 0.06)]),
    ),
    "fall": _act(
        1000,
        150,
        250,
        True,
        _rot("head", "x", [(0, -0.15)]),
        _rot("shoulder_L", "z", [(0, 0.40)]),
        _rot("shoulder_R", "z", [(0, -0.40)]),
    ),
    "land_squash": _act(
        400,
        60,
        200,
        False,
        _scale("body_main", "y", [(0, 0.90)]),
        _scale("body_main", "x", [(0, 1.08)]),
    ),
    "peeking": _act(
        2000,
        300,
        300,
        True,
        _rot("head", "y", [(0, -0.22)]),
        _rot("head", "z", [(0, 0.05)]),
    ),
    "click": _act(
        600,
        80,
        200,
        False,
        _rot("shoulder_R", "z", [(0, -0.45)]),
        _rot("elbow_R", "z", [(0, -0.55)]),
        _rot("wrist_R", "z", [(0, -0.50), (180, -0.30), (360, -0.44), (600, -0.32)], "ease_in_out"),
        _rot("head", "y", [(0, 0.08)]),
    ),
    "long_press": _act(
        1200,
        250,
        350,
        False,
        _rot("head", "z", [(0, 0.10)]),
        _rot("head", "x", [(0, -0.06)]),
    ),
}


# 本地物理 / 交互触发动作：脱离触发上下文播放会是悬空姿态，注入 LLM 清单时排除。
NON_LLM_ACTIONS: frozenset[str] = frozenset(
    {"fall", "land_squash", "peeking", "click", "long_press"},
)


# idle variants：每个本质是一个完整的 action 描述（可能被 action 表复用）。
DEFAULT_IDLE_VARIANTS: list[str] = [
    "idle_breath",
    "idle_glance",
    "idle_squint",
    "idle_sway_more",
    "idle_stretch",
    "idle_hip_shift",
    "idle_lean_back",
]

# 复用 DEFAULT_ACTIONS 里的 idle_glance；剩余的 idle_* 在此就地声明（也只读不写入 bone）。
DEFAULT_IDLE_VARIANT_BODIES: dict[str, ActionDef] = {
    # 仅驱动 front_hair 微缩放 + 不改 head，避免与 breath / sway 冲突
    "idle_breath": _act(3000, 300, 400, False, _scale("front_hair", "y", [(0, 1.02)])),
    # eye 骨骼通过 blink 通道已有 scale.y，这里仅点头下视
    "idle_squint": _act(600, 200, 250, False, _rot("head", "x", [(0, -0.06)])),
    "idle_sway_more": _act(3500, 300, 300, False, _rot("body_main", "z", [(0, 0.05)])),
    # 伸懒腰：双肩上提-保持-回落 + 头微仰
    "idle_stretch": _act(
        3600,
        300,
        500,
        False,
        _rot("shoulder_L", "z", [(0, 0.10), (400, 0.35), (2600, 0.33), (3600, 0.08)], "ease_in_out"),
        _rot("shoulder_R", "z", [(0, -0.10), (400, -0.35), (2600, -0.33), (3600, -0.08)], "ease_in_out"),
        _rot("head", "x", [(0, -0.02), (400, -0.08), (2600, -0.07), (3600, -0.02)], "ease_in_out"),
    ),
    # 换重心：躯干侧倾 + 头反向代偿
    "idle_hip_shift": _act(
        4000,
        300,
        400,
        False,
        _rot("body_main", "z", [(0, 0.015), (400, 0.045), (3400, 0.042), (4000, 0.012)], "ease_in_out"),
        _rot("head", "y", [(0, -0.03), (400, -0.08), (3400, -0.075), (4000, -0.025)], "ease_in_out"),
    ),
    # 后仰放松：躯干后仰 + 头抬起 + 双肩微后收
    "idle_lean_back": _act(
        3200,
        300,
        450,
        False,
        _rot("body_main", "x", [(0, 0.02), (350, 0.07), (2700, 0.065), (3200, 0.018)], "ease_in_out"),
        _rot("head", "x", [(0, 0.03), (350, 0.09), (2700, 0.085), (3200, 0.028)], "ease_in_out"),
    ),
}


def get_default_action_table() -> dict[str, ActionDef]:
    """对外暴露合并后的动作表（DEFAULT_ACTIONS ∪ DEFAULT_IDLE_VARIANT_BODIES）。"""
    return {**DEFAULT_ACTIONS, **DEFAULT_IDLE_VARIANT_BODIES}


def _action_to_dict(action: ActionDef) -> dict[str, Any]:
    # exclude_defaults 丢弃 ease="linear"，保持 JSON 紧凑。
    return {
        "duration_ms": action.duration_ms,
        "blend_in_ms": action.blend_in_ms,
        "blend_out_ms": action.blend_out_ms,
        "loop": action.loop,
        "tracks": [track.model_dump(exclude_defaults=True) for track in action.tracks],
    }


# locomotion：周期公式由 driver 按 sin(t) 求值；jump 是单次脉冲。
# bone 列表只用 body_main / shoulder_L / shoulder_R / skirt / back_hair，
# 因为 hip/knee/ankle 绑定的部件（body_main.png）旋转不会改变 mesh2d 画面。
DEFAULT_LOCOMOTION: dict[str, dict[str, Any]] = {
    "still": {"bones": {}},  # 占位：不应用任何相位
    "walk": {
        "bones": {
            "body_main": {
                "amplitude_rad": 0.03,
                "period_ms": 800,
                "phase_offset": 0.0,
                "axis": "z",
            },
            "shoulder_L": {
                "amplitude_rad": 0.25,
                "period_ms": 800,
                "phase_offset": 0.0,
                "axis": "z",
            },
            "shoulder_R": {
                "amplitude_rad": 0.25,
                "period_ms": 800,
                "phase_offset": 3.14159,
                "axis": "z",
            },
            # body_main.scale.y 周期弹跳（bob）—— 通过 amplitude_scale 字段表达
            "body_main__scale_y_bob": {
                "amplitude_scale": 0.008,
                "period_ms": 800,
                "phase_offset": 0.0,
            },
            # skirt / back_hair 持续 impulse 触发
            "skirt_impulse": {"magnitude": 2.5, "period_ms": 400},
            "back_hair_impulse": {"magnitude": 1.8, "period_ms": 400},
        },
    },
    "walk_fast": {
        "bones": {
            "body_main": {
                "amplitude_rad": 0.04,
                "period_ms": 550,
                "phase_offset": 0.0,
                "axis": "z",
            },
            "shoulder_L": {
                "amplitude_rad": 0.32,
                "period_ms": 550,
                "phase_offset": 0.0,
                "axis": "z",
            },
            "shoulder_R": {
                "amplitude_rad": 0.32,
                "period_ms": 550,
                "phase_offset": 3.14159,
                "axis": "z",
            },
            "body_main__scale_y_bob": {
                "amplitude_scale": 0.010,
                "period_ms": 550,
                "phase_offset": 0.0,
            },
            "skirt_impulse": {"magnitude": 3.0, "period_ms": 275},
            "back_hair_impulse": {"magnitude": 2.2, "period_ms": 275},
        },
    },
    "fly": {"bones": {}},  # fly 不引入额外骨骼摆动（位置由 spatial 处理）
    "drag": {"bones": {}},
    # 单次脉冲：preload（squash 压缩）+ hold（保持）+ recover（回弹）。
    # driver 在 jump 触发时按三段分别调用 scale.y clamp 到 ≤1.015 红线内。
    "jump": {
        "pulse": {
            "bone": "body_main",
            "preload_ms": 90,
            "hold_ms": 50,
            "recover_ms": 220,
            "scale_y_min": 1.012,  # squash 时轻微下压（不超红线）
            "shoulder_lift_rad": 0.6,  # shoulder_L/R 同时上扬
        },
    },
}


DEFAULT_ANIMATIONS: dict[str, Any] = {
    "breath": {"amplitude": 0.015, "period_ms": 3400},
    "blink": {"min_period_ms": 3000, "max_period_ms": 6000, "duration_ms": 120},
    "idle_sway": {"amplitude": 0.04, "min_period_ms": 5000, "max_period_ms": 8000},
    "jiggle": {
        "hair_back_root": {"k": 80, "c": 6, "impulse_decay": 0.92},
        "skirt_root": {"k": 60, "c": 5, "impulse_decay": 0.93},
        "bust": {"k": 100, "c": 8, "impulse_decay": 0.94},
    },
    # 骨骼 transform 红线（驱动层兜底 clamp）。客户端应从 manifest 读取，不要硬编码。
    "red_lines": _BONE_RED_LINES,
    "actions": {name: _action_to_dict(action) for name, action in get_default_action_table().items()},
    "idle_variants": list(DEFAULT_IDLE_VARIANTS),
    "locomotion": DEFAULT_LOCOMOTION,
}


def build_manifest(
    bones: list[BoneDef],
    meshes: list[MeshDef],
    canvas: tuple[int, int],
) -> Manifest:
    manifest = Manifest()
    manifest.canvas = {"w": canvas[0], "h": canvas[1]}
    manifest.skeleton = {"bones": [_bone_to_dict(b) for b in bones]}
    manifest.meshes = [_mesh_to_dict(m) for m in meshes]
    manifest.animations = DEFAULT_ANIMATIONS
    return manifest
