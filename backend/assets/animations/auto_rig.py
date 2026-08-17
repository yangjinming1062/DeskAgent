"""Blender headless auto-rigging for cloud image-to-3D providers without a
rig API. Consumes a JSON skeleton spec (normalized bbox fractions produced by
``services/companion/rig_layout.py``), builds the armature, parents every
mesh with automatic bone-heat weights and exports a rigged GLB.

    blender --background --python auto_rig.py -- \
        --input in.glb --output out.glb --spec rig_spec.json
"""

import argparse
import json
import sys
from pathlib import Path

import bpy
from mathutils import Vector

_MIN_BONE_LENGTH = 1e-3


def _parse_args(argv: list[str]) -> argparse.Namespace:
    argv = argv[argv.index("--") + 1 :] if "--" in argv else []
    p = argparse.ArgumentParser(prog="auto_rig")
    p.add_argument("--input", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--spec", required=True, help="JSON skeleton spec from rig_layout.layout_skeleton")
    return p.parse_args(argv)


def _world_bbox(objects: list[bpy.types.Object]) -> tuple[Vector, Vector]:
    mn = Vector((1e9, 1e9, 1e9))
    mx = Vector((-1e9, -1e9, -1e9))
    for obj in objects:
        for corner in obj.bound_box:
            wc = obj.matrix_world @ Vector(corner)
            mn = Vector(min(a, b) for a, b in zip(mn, wc))
            mx = Vector(max(a, b) for a, b in zip(mx, wc))
    return mn, mx


def main() -> int:
    args = _parse_args(sys.argv)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=args.input)
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not meshes:
        print("auto_rig: imported GLB contains no mesh", file=sys.stderr)
        return 1

    mn, mx = _world_bbox(meshes)
    size = Vector(max(c - d, _MIN_BONE_LENGTH) for c, d in zip(mx, mn))
    center = Vector(((mn.x + mx.x) / 2, mn.y, (mn.z + mx.z) / 2))

    def to_world(frac: list[float]) -> Vector:
        return Vector((center.x + frac[0] * size.x, center.y + frac[1] * size.y, center.z + frac[2] * size.z))

    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))

    arm_data = bpy.data.armatures.new("Armature")
    arm_obj = bpy.data.objects.new("Armature", arm_data)
    bpy.context.scene.collection.objects.link(arm_obj)
    bpy.context.view_layer.objects.active = arm_obj
    bpy.ops.object.mode_set(mode="EDIT")
    for bone in spec["bones"]:
        eb = arm_data.edit_bones.new(bone["name"])
        eb.head = to_world(bone["head"])
        eb.tail = to_world(bone["tail"])
        if (eb.tail - eb.head).length < _MIN_BONE_LENGTH:
            eb.tail = eb.head + Vector((0.0, _MIN_BONE_LENGTH, 0.0))
        if bone["parent"]:
            eb.parent = arm_data.edit_bones[bone["parent"]]
    bpy.ops.object.mode_set(mode="OBJECT")

    bpy.ops.object.select_all(action="DESELECT")
    for obj in meshes:
        obj.select_set(True)
    arm_obj.select_set(True)
    bpy.context.view_layer.objects.active = arm_obj
    bpy.ops.object.parent_set(type="ARMATURE_AUTO")

    bpy.ops.export_scene.gltf(filepath=args.output, export_format="GLB", export_draco_mesh_compression_enable=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
