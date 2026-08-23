"""manifest.json 生成与序列化。"""

import json
from dataclasses import dataclass, field
from typing import Any

from .skeleton_builder import BoneDef, MeshDef

SCHEMA_VERSION = 1


@dataclass
class Manifest:
    schema: str = "spiritagent.mesh2d/1"
    version: int = SCHEMA_VERSION
    canvas: dict[str, int] = field(default_factory=lambda: {"w": 1024, "h": 1366})
    camera: dict[str, Any] = field(default_factory=lambda: {"type": "orthographic", "zoom": 1.0})
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


DEFAULT_ANIMATIONS: dict[str, Any] = {
    "breath": {"amplitude": 0.015, "period_ms": 3400},
    "blink": {"min_period_ms": 3000, "max_period_ms": 6000, "duration_ms": 120},
    "idle_sway": {"amplitude": 0.04, "min_period_ms": 5000, "max_period_ms": 8000},
    "jiggle": {
        "hair_back_root": {"k": 80, "c": 6, "impulse_decay": 0.92},
        "skirt_root": {"k": 60, "c": 5, "impulse_decay": 0.93},
        "bust": {"k": 100, "c": 8, "impulse_decay": 0.94},
    },
}


def build_manifest(bones: list[BoneDef], meshes: list[MeshDef], canvas: tuple[int, int]) -> Manifest:
    manifest = Manifest()
    manifest.canvas = {"w": canvas[0], "h": canvas[1]}
    manifest.skeleton = {"bones": [_bone_to_dict(b) for b in bones]}
    manifest.meshes = [_mesh_to_dict(m) for m in meshes]
    manifest.animations = DEFAULT_ANIMATIONS
    return manifest
