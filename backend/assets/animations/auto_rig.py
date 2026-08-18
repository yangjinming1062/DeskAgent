"""Blender headless auto-rigging for cloud image-to-3D providers without a
rig API. Consumes a JSON skeleton spec (normalized bbox fractions produced by
``services/companion/rig_layout.py``), builds the armature, parents every
mesh with automatic bone-heat weights and exports a rigged GLB.

    blender --background --python auto_rig.py -- \
        --input in.glb --output out.glb --spec rig_spec.json
"""

import argparse
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

_MIN_BONE_LENGTH = 1e-3

# Bone heat solves reliably at this scale; the million-vertex text-to-3D
# meshes diverge outright. Above it, weights are solved on a decimated
# proxy and transferred back (the solved deformation matters, not the
# vertex count it was solved on).
_PROXY_MAX_VERTICES: int = 120_000


def _parse_args(argv: list[str]) -> argparse.Namespace:
    argv = argv[argv.index("--") + 1 :] if "--" in argv else []
    p = argparse.ArgumentParser(prog="auto_rig")
    p.add_argument("--input", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--spec", required=True, help="JSON skeleton spec from rig_layout.layout_skeleton")
    p.add_argument("--yaw", type=float, default=0.0, help="Skeleton yaw in degrees: face direction of the mesh vs the canonical -Y front (from the vision-LLM face detection).")
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


def _parent_auto(objects: list[bpy.types.Object], arm_obj: bpy.types.Object) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    arm_obj.select_set(True)
    bpy.context.view_layer.objects.active = arm_obj
    bpy.ops.object.parent_set(type="ARMATURE_AUTO")


def _weights_complete(objects: list[bpy.types.Object]) -> bool:
    return all(any(g.weight > 0.0 for g in v.groups) for obj in objects for v in obj.data.vertices)


def _make_proxy(obj: bpy.types.Object) -> bpy.types.Object:
    proxy = obj.copy()
    proxy.data = obj.data.copy()
    bpy.context.scene.collection.objects.link(proxy)
    ratio = min(1.0, _PROXY_MAX_VERTICES / max(len(proxy.data.vertices), 1))
    if ratio < 1.0:
        mod = proxy.modifiers.new("Decimate", "DECIMATE")
        mod.ratio = ratio
        bpy.context.view_layer.objects.active = proxy
        bpy.ops.object.modifier_apply(modifier=mod.name)
    return proxy


def _transfer_weights(proxy: bpy.types.Object, obj: bpy.types.Object, arm_obj: bpy.types.Object) -> None:
    # Data transfer fills only target groups that already exist, matched by name.
    for bone in arm_obj.data.bones:
        obj.vertex_groups.new(name=bone.name)
    mod = obj.modifiers.new("WeightTransfer", "DATA_TRANSFER")
    mod.object = proxy
    mod.use_object_transform = True
    mod.use_vert_data = True
    mod.data_types_verts = {"VGROUP_WEIGHTS"}
    mod.vert_mapping = "POLYINTERP_NEAREST"
    mod.mix_mode = "REPLACE"
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.modifier_apply(modifier=mod.name)


def _normalize_weights(obj: bpy.types.Object) -> None:
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.object.vertex_group_normalize_all()
    bpy.ops.object.mode_set(mode="OBJECT")


def _assign_strays_to_nearest_bone(obj: bpy.types.Object, arm_obj: bpy.types.Object) -> int:
    """GLB skinning needs nonzero vertex-group weights everywhere — a vertex
    the solver left bare is rigid forever, tearing the mesh on first pose.
    Rescue it with a unit weight on the nearest bone."""
    stray = [v.index for v in obj.data.vertices if not any(g.weight > 0.0 for g in v.groups)]
    if not stray:
        return 0
    heads = [(arm_obj.matrix_world @ bone.head_local, bone.name) for bone in arm_obj.data.bones]
    for vi in stray:
        world = obj.matrix_world @ obj.data.vertices[vi].co
        name = min(heads, key=lambda t: (t[0] - world).length)[1]
        obj.vertex_groups[name].add([vi], 1.0, "REPLACE")
    return len(stray)


def _face_yaw(args: argparse.Namespace) -> float:
    """Skeleton yaw in radians from the ``--yaw`` degree argument. The spec
    skeleton faces Blender -Y; text-to-3D meshes ship in arbitrary yaw, which
    would leave arm bones floating beside (heat-binding onto) the torso. The
    caller resolves the face direction beforehand — a vision LLM reading four
    view snapshots; head-vertex-density heuristics proved unreliable (long
    hair flattens the contrast between face and back of skull)."""
    return math.radians(args.yaw)


def main() -> int:
    args = _parse_args(sys.argv)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=args.input)
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not meshes:
        print("auto_rig: imported GLB contains no mesh", file=sys.stderr)
        return 1

    # glTF import leaves the +90° X axis conversion on the object while the
    # data is already upright — re-exporting applies it twice and the mesh
    # ships lying. Bake it into the data (world pose unchanged).
    bpy.ops.object.select_all(action="DESELECT")
    for obj in meshes:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)

    # Bone heat fails outright on non-manifold provider meshes (zero weights
    # → skinless export); a no-op 1e-5 remove_doubles rebuilds the BMesh
    # enough for the solve to converge.
    for obj in meshes:
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.mesh.remove_doubles(threshold=1e-5)
        bpy.ops.object.mode_set(mode="OBJECT")

    mn, mx = _world_bbox(meshes)
    size = Vector(max(c - d, _MIN_BONE_LENGTH) for c, d in zip(mx, mn))
    center = Vector(((mn.x + mx.x) / 2, (mn.y + mx.y) / 2, mn.z))

    # Spec is glTF-convention (Y up, front -Z, left -X); remap into Blender's
    # Z-up world where imported meshes front -Y with the character's left at +X.
    def to_world(frac: list[float]) -> Vector:
        return Vector((center.x - frac[0] * size.x, center.y + frac[2] * size.y, center.z + frac[1] * size.z))

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
            eb.tail = eb.head + Vector((0.0, 0.0, _MIN_BONE_LENGTH))
        if bone["parent"]:
            eb.parent = arm_data.edit_bones[bone["parent"]]
    bpy.ops.object.mode_set(mode="OBJECT")

    yaw = _face_yaw(args)
    if abs(yaw) > 1e-3:
        arm_obj.rotation_mode = "XYZ"
        arm_obj.rotation_euler.rotate_axis("Z", yaw)

    _parent_auto(meshes, arm_obj)

    if not _weights_complete(meshes):
        proxies = [_make_proxy(obj) for obj in meshes]
        _parent_auto(proxies, arm_obj)
        # Partial heat failure keeps some bones bare; rescue before transfer.
        for proxy in proxies:
            strays = _assign_strays_to_nearest_bone(proxy, arm_obj)
            if strays:
                print(f"auto_rig: {strays} proxy vertices fell back to nearest-bone", file=sys.stderr)
        for proxy, obj in zip(proxies, meshes):
            _transfer_weights(proxy, obj, arm_obj)
            _normalize_weights(obj)
            strays = _assign_strays_to_nearest_bone(obj, arm_obj)
            if strays:
                print(f"auto_rig: {strays} vertices fell back to nearest-bone", file=sys.stderr)
            bpy.data.objects.remove(proxy, do_unlink=True)
        if not _weights_complete(meshes):
            print("auto_rig: weight transfer left vertices unskinned", file=sys.stderr)
            return 1

    if abs(yaw) > 1e-3:
        # Rotate rig + mesh together back to the canonical -Y front, then
        # bake — the glTF exporter drops unapplied object rotations, and
        # writing rotation_euler is a no-op on quaternion-mode imports.
        back = Matrix.Rotation(-yaw, 4, "Z")
        for obj in bpy.context.scene.objects:
            if obj.parent is None:
                obj.matrix_world = back @ obj.matrix_world
        bpy.ops.object.select_all(action="SELECT")
        bpy.context.view_layer.objects.active = arm_obj
        bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)

    bpy.ops.export_scene.gltf(filepath=args.output, export_format="GLB", export_draco_mesh_compression_enable=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
