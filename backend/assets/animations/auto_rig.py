"""Blender headless 自动绑骨：读取 JSON 骨架规格（来自 rig_layout.layout_skeleton 的归一化 bbox 分数），构建 armature、用 bone-heat 自动蒙皮并导出 rigged GLB。"""

import argparse
import json
import math
import sys
from pathlib import Path

import bpy
from auto_rig_helpers import sanitize_head_weights
from mathutils import Matrix, Vector

_MIN_BONE_LENGTH = 1e-3

# bone-heat 在该顶点数下求解稳定；百万顶点级的文生 3D 网格会直接发散。超过此值则在减面代理上求解再回传权重（关键是解得的形变，而不是求解时的顶点数）。
_PROXY_MAX_VERTICES: int = 120_000


def _parse_args(argv: list[str]) -> argparse.Namespace:
    argv = argv[argv.index("--") + 1 :] if "--" in argv else []
    p = argparse.ArgumentParser(prog="auto_rig")
    p.add_argument("--input", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--spec", required=False, help="JSON skeleton spec from rig_layout.layout_skeleton")
    p.add_argument(
        "--mode",
        choices=["rig", "normalize"],
        default="rig",
        help="rig: build + skin from spec; normalize: post-process an already-cloud-rigged GLB (strip bone prefixes, canonical yaw).",
    )
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


def _validate_automatic_weights(objects: list[bpy.types.Object], arm_obj: bpy.types.Object) -> None:
    """在用最近骨补救权重前，拦截静默的 bone-heat 求解失败。"""
    critical_bones = [bone for bone in arm_obj.data.bones if not bone.children and bone.name.lower().startswith(("left", "right")) and not bone.name.lower().endswith("eye")]
    for bone in critical_bones:
        if not any(any(group.weight > 0.0 for group in vertex.groups if obj.vertex_groups[group.group].name == bone.name) for obj in objects for vertex in obj.data.vertices):
            raise RuntimeError(f"automatic bone weights left {bone.name} without influence")


def _make_proxy(obj: bpy.types.Object) -> bpy.types.Object:
    proxy = obj.copy()
    proxy.data = obj.data.copy()
    bpy.context.scene.collection.objects.link(proxy)
    bpy.context.view_layer.objects.active = proxy
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.remove_doubles(threshold=1e-5)
    bpy.ops.object.mode_set(mode="OBJECT")
    ratio = min(1.0, _PROXY_MAX_VERTICES / max(len(proxy.data.vertices), 1))
    if ratio < 1.0:
        mod = proxy.modifiers.new("Decimate", "DECIMATE")
        mod.ratio = ratio
        bpy.context.view_layer.objects.active = proxy
        bpy.ops.object.modifier_apply(modifier=mod.name)
    return proxy


def _transfer_weights(proxy: bpy.types.Object, obj: bpy.types.Object, arm_obj: bpy.types.Object) -> None:
    # Data Transfer 只填充目标已存在的同名顶点组。
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
    """GLB 蒙皮要求每个顶点都带非零顶点组权重；bone-heat 求解遗漏的顶点会永远刚性、首次姿态就撕裂网格。用最近骨单位权重兜底。"""
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
    """把 ``--yaw`` 度数转弧度：规格骨架面朝 Blender -Y；文生 3D 网格朝向任意，若不旋转会导致手臂骨"漂"在躯干侧面（被 heat 绑到躯干上）。朝向由调用方预先解析——视觉 LLM 读四张快照；头部顶点密度启发式不可靠（长发会拉平面部与颅背的对比）。"""
    return math.radians(args.yaw)


def _export(path: str) -> None:
    bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", export_normals=False, export_draco_mesh_compression_enable=False)


def _apply_canonical_yaw(yaw: float) -> None:
    """把 rig 与网格一起旋转到 -Y 朝前并烘焙：glTF 导出器会丢弃未应用的物体旋转，而 quaternion 模式下写 rotation_euler 不生效。"""
    if abs(yaw) <= 1e-3:
        return
    back = Matrix.Rotation(-yaw, 4, "Z")
    for obj in bpy.context.scene.objects:
        if obj.parent is None:
            obj.matrix_world = back @ obj.matrix_world
    arm_obj = next((o for o in bpy.context.scene.objects if o.type == "ARMATURE"), None)
    bpy.ops.object.select_all(action="SELECT")
    if arm_obj is not None:
        bpy.context.view_layer.objects.active = arm_obj
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)


def _strip_bone_prefixes() -> int:
    """把 ``mixamorig:X`` 重命名为 ``X``：云端 rig 的 GLB 带前缀，客户端 clip 追踪的目标是裸名。顶点组同步重命名（仅重命名 edit bone 不会带动顶点组）。"""
    renamed = 0
    for arm_obj in (o for o in bpy.context.scene.objects if o.type == "ARMATURE"):
        renames = {b.name: b.name.split(":")[-1] for b in arm_obj.data.bones if ":" in b.name}
        if not renames:
            continue
        bpy.context.view_layer.objects.active = arm_obj
        bpy.ops.object.mode_set(mode="EDIT")
        for eb in arm_obj.data.edit_bones:
            if eb.name in renames:
                eb.name = renames[eb.name]
        bpy.ops.object.mode_set(mode="OBJECT")
        for obj in bpy.context.scene.objects:
            if obj.type == "MESH":
                for vg in obj.vertex_groups:
                    if vg.name in renames:
                        vg.name = renames[vg.name]
        renamed += len(renames)
    return renamed


def _normalize_cloud_rigged(args: argparse.Namespace) -> int:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=args.input)
    armature = next((o for o in bpy.context.scene.objects if o.type == "ARMATURE"), None)
    if armature is None:
        print("auto_rig: normalize input contains no armature", file=sys.stderr)
        return 1
    _strip_bone_prefixes()
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    corrected = sanitize_head_weights(meshes, armature)
    if corrected:
        print(f"auto_rig: cleaned appendage weights on {corrected} head-region vertices", file=sys.stderr)
    _apply_canonical_yaw(_face_yaw(args))
    _export(args.output)
    return 0


def main() -> int:
    args = _parse_args(sys.argv)

    if args.mode == "normalize":
        return _normalize_cloud_rigged(args)
    if not args.spec:
        print("auto_rig: --spec is required in rig mode", file=sys.stderr)
        return 1

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=args.input)
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not meshes:
        print("auto_rig: imported GLB contains no mesh", file=sys.stderr)
        return 1

    # glTF 导入把 +90° X 轴换算留在物体上，但数据本身已正立；再导出相当于应用两次，网格躺着落地。烘焙到数据里（世界姿态不变）。
    bpy.ops.object.select_all(action="DESELECT")
    for obj in meshes:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)

    mn, mx = _world_bbox(meshes)
    size = Vector(max(c - d, _MIN_BONE_LENGTH) for c, d in zip(mx, mn))
    center = Vector(((mn.x + mx.x) / 2, (mn.y + mx.y) / 2, mn.z))

    # 规格使用 glTF 约定（Y 朝上，前方 -Z，左侧 -X）；重映射到 Blender 的 Z 朝上世界（导入后网格前方 -Y、角色左侧在 +X）。
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

    proxies = [_make_proxy(obj) for obj in meshes]
    _parent_auto(proxies, arm_obj)
    _validate_automatic_weights(proxies, arm_obj)
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
    sanitize_head_weights(meshes, arm_obj)
    if not _weights_complete(meshes):
        print("auto_rig: weight transfer left vertices unskinned", file=sys.stderr)
        return 1

    if abs(yaw) > 1e-3:
        _apply_canonical_yaw(yaw)

    _export(args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
