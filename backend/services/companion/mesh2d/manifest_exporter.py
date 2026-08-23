"""manifest.json 生成与序列化。"""

import json
from dataclasses import dataclass, field
from typing import Any

from .skeleton_builder import BoneDef, MeshDef

SCHEMA_VERSION = 2  # v2: 加入 actions / idle_variants / locomotion 字段


@dataclass
class Manifest:
    schema: str = "spiritagent.mesh2d/1"
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
# - rotation_rad 字段命名强约束"弧度"，消费端 Three.js 用 .rotation 直接赋值，不做 degToRad。
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


# 单次动作（LLM 触发或交互触发）。所有 rotation_rad 单位都是弧度。
DEFAULT_ACTIONS: dict[str, dict[str, Any]] = {
    "wave_right": {
        "duration_ms": 1800,
        "blend_in_ms": 250,
        "blend_out_ms": 350,
        "loop": False,
        "bones": {
            "shoulder_R": {"rotation_rad": {"z": -0.52}},
            "elbow_R": {"rotation_rad": {"z": -0.78}},
            "wrist_R": {"rotation_rad": {"z": -1.40}},
            "head": {"rotation_rad": {"y": 0.18}},
        },
    },
    "wave_left": {
        "duration_ms": 1800,
        "blend_in_ms": 250,
        "blend_out_ms": 350,
        "loop": False,
        "bones": {
            "shoulder_L": {"rotation_rad": {"z": 0.52}},
            "elbow_L": {"rotation_rad": {"z": 0.78}},
            "wrist_L": {"rotation_rad": {"z": 1.40}},
            "head": {"rotation_rad": {"y": -0.18}},
        },
    },
    "present_right": {
        "duration_ms": 1200,
        "blend_in_ms": 200,
        "blend_out_ms": 300,
        "loop": False,
        "bones": {
            "shoulder_R": {"rotation_rad": {"z": -0.26}},
            "elbow_R": {"rotation_rad": {"z": -0.52}},
            "wrist_R": {"rotation_rad": {"z": -0.79}},
        },
    },
    "present_left": {
        "duration_ms": 1200,
        "blend_in_ms": 200,
        "blend_out_ms": 300,
        "loop": False,
        "bones": {
            "shoulder_L": {"rotation_rad": {"z": 0.26}},
            "elbow_L": {"rotation_rad": {"z": 0.52}},
            "wrist_L": {"rotation_rad": {"z": 0.79}},
        },
    },
    "look_away_left": {
        "duration_ms": 1200,
        "blend_in_ms": 250,
        "blend_out_ms": 350,
        "loop": False,
        "bones": {
            "head": {"rotation_rad": {"y": -0.35}},
            "body_main": {"rotation_rad": {"y": -0.08}},
        },
    },
    "look_away_right": {
        "duration_ms": 1200,
        "blend_in_ms": 250,
        "blend_out_ms": 350,
        "loop": False,
        "bones": {
            "head": {"rotation_rad": {"y": 0.35}},
            "body_main": {"rotation_rad": {"y": 0.08}},
        },
    },
    "turn_body_left": {
        "duration_ms": 2000,
        "blend_in_ms": 300,
        "blend_out_ms": 400,
        "loop": False,
        "bones": {
            "body_main": {"rotation_rad": {"y": -0.18}},
            "head": {"rotation_rad": {"y": -0.10}},
        },
    },
    "turn_body_right": {
        "duration_ms": 2000,
        "blend_in_ms": 300,
        "blend_out_ms": 400,
        "loop": False,
        "bones": {
            "body_main": {"rotation_rad": {"y": 0.18}},
            "head": {"rotation_rad": {"y": 0.10}},
        },
    },
    "lean_forward": {
        "duration_ms": 1500,
        "blend_in_ms": 300,
        "blend_out_ms": 400,
        "loop": False,
        "bones": {
            "body_main": {"rotation_rad": {"x": -0.10}},
            "neck": {"rotation_rad": {"x": 0.08}},
        },
    },
    "shy": {
        "duration_ms": 2000,
        "blend_in_ms": 300,
        "blend_out_ms": 500,
        "loop": False,
        "bones": {
            "head": {"rotation_rad": {"y": 0.25, "x": -0.10}},
            "front_hair": {"scale": {"y": 1.05}},
        },
    },
    "idle_glance": {
        "duration_ms": 800,
        "blend_in_ms": 200,
        "blend_out_ms": 200,
        "loop": False,
        "bones": {
            "head": {"rotation_rad": {"y": -0.20}},
        },
    },
}


# idle variants：每个本质是一个完整的 action 描述（可能被 action 表复用）。
DEFAULT_IDLE_VARIANTS: list[str] = [
    "idle_breath",
    "idle_glance",
    "idle_squint",
    "idle_sway_more",
]

# 复用 DEFAULT_ACTIONS 里的 idle_glance；剩余的 idle_* 在此就地声明（也只读不写入 bone）。
DEFAULT_IDLE_VARIANT_BODIES: dict[str, dict[str, Any]] = {
    "idle_breath": {
        "duration_ms": 3000,
        "blend_in_ms": 300,
        "blend_out_ms": 400,
        "loop": False,
        # 仅驱动 front_hair 微缩放 + 不改 head，避免与 breath / sway 冲突
        "bones": {"front_hair": {"scale": {"y": 1.02}}},
    },
    "idle_squint": {
        "duration_ms": 600,
        "blend_in_ms": 200,
        "blend_out_ms": 250,
        "loop": False,
        # eye 骨骼通过 blink 通道已有 scale.y，这里仅点头下视
        "bones": {"head": {"rotation_rad": {"x": -0.06}}},
    },
    "idle_sway_more": {
        "duration_ms": 3500,
        "blend_in_ms": 300,
        "blend_out_ms": 300,
        "loop": False,
        "bones": {"body_main": {"rotation_rad": {"z": 0.05}}},
    },
}


def get_default_action_table() -> dict[str, dict[str, Any]]:
    """对外暴露合并后的动作表（DEFAULT_ACTIONS ∪ DEFAULT_IDLE_VARIANT_BODIES）。"""
    return {**DEFAULT_ACTIONS, **DEFAULT_IDLE_VARIANT_BODIES}


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
    "actions": {**DEFAULT_ACTIONS, **DEFAULT_IDLE_VARIANT_BODIES},
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
