import argparse
import json
import sys

import bmesh
import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree

_SHRINKWRAP_OFFSET = 0.0015
_SOLIDIFY_THICKNESS = 0.002
_SUBSURF_VERT_THRESHOLD = 4000
_COLLISION_CLEARANCE = 0.003


def _build_garment(ctx: dict) -> None:  # pragma: no cover  -- replaced at runtime
    __BUILD_GARMENT__  # noqa: F821 -- placeholder marker


def _parse_args(argv: list[str]) -> argparse.Namespace:
    argv = argv[argv.index("--") + 1 :] if "--" in argv else argv
    p = argparse.ArgumentParser(prog="garment_bpy_scaffold")
    p.add_argument("--output", required=True)
    p.add_argument("--body-glb", required=True)
    p.add_argument("--render-output", default="")
    p.add_argument("--assembly", default="")
    p.add_argument("--kind", choices=("garment", "accessory"), default="garment")
    p.add_argument("--socket", default="")
    return p.parse_args(argv)


def _reset_scene() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)


def _import_body_glb(path: str) -> tuple[bpy.types.Object, bpy.types.Object, list[bpy.types.Object]]:
    bpy.ops.import_scene.gltf(filepath=path)
    imported = list(bpy.context.scene.objects)
    armatures = [o for o in imported if o.type == "ARMATURE"]
    meshes = [o for o in imported if o.type == "MESH"]
    if not armatures:
        raise RuntimeError("body GLB has no armature — garment pipeline requires a rigged body")
    if not meshes:
        raise RuntimeError("body GLB has no mesh")
    body_mesh = max(meshes, key=lambda m: len(m.data.vertices))
    return armatures[0], body_mesh, imported


def _build_ctx(armature: bpy.types.Object, body_mesh: bpy.types.Object, socket: str) -> dict:
    bones: dict[str, dict] = {}
    for bone in armature.data.bones:
        bones[bone.name] = {"head": tuple(bone.head_local), "tail": tuple(bone.tail_local), "length": bone.length}
    bb = [Vector(c) for c in body_mesh.bound_box]
    bb_world = [body_mesh.matrix_world @ c for c in bb]
    body_bounds = {"min": tuple(min(v[i] for v in bb_world) for i in range(3)), "max": tuple(max(v[i] for v in bb_world) for i in range(3))}
    return {"body": {"armature": armature, "mesh": body_mesh}, "bones": bones, "body_bounds": body_bounds, "params": {"socket": socket} if socket else {}}


def _fit_garment(garment: bpy.types.Object, body_mesh: bpy.types.Object) -> None:
    if "VG_ANCHOR" not in garment.vertex_groups:
        raise RuntimeError(f"garment '{garment.name}' missing VG_ANCHOR vertex group")
    bpy.context.view_layer.objects.active = garment
    garment.select_set(True)
    mod = garment.modifiers.new(name="GarmentFit", type="SHRINKWRAP")
    mod.target = body_mesh
    mod.wrap_method = "NEAREST_SURFACEPOINT"
    mod.vertex_group = "VG_ANCHOR"
    mod.offset = _SHRINKWRAP_OFFSET
    bpy.ops.object.modifier_apply(modifier=mod.name)


def _drape_garment(garment: bpy.types.Object, body_mesh: bpy.types.Object, frames: int = 20) -> None:
    """把静态重力垂落烘焙成单层 rest geometry，再做加厚与蒙皮。Blender 5.2 在 cloth mass 顶点组上权重 1.0 的顶点被原地锁住，没有独立的 pin 组。"""
    bpy.context.view_layer.objects.active = body_mesh
    body_mesh.select_set(True)
    body_col = body_mesh.modifiers.new(name="BodyCollision", type="COLLISION")

    bpy.context.view_layer.objects.active = garment
    garment.select_set(True)
    cloth = garment.modifiers.new(name="GarmentDrape", type="CLOTH")
    if "VG_ANCHOR" in garment.vertex_groups:
        cloth.settings.vertex_group_mass = "VG_ANCHOR"
    cloth.collision_settings.use_collision = True
    cloth.collision_settings.distance_min = _COLLISION_CLEARANCE

    for f in range(1, frames + 1):
        bpy.context.scene.frame_set(f)

    bpy.ops.object.modifier_apply(modifier=cloth.name)
    bpy.context.scene.frame_set(1)

    if body_col.name in body_mesh.modifiers:
        body_mesh.modifiers.remove(body_col)


def _solidify_garment(garment: bpy.types.Object) -> None:
    bpy.context.view_layer.objects.active = garment
    garment.select_set(True)
    solidify = garment.modifiers.new(name="GarmentThickness", type="SOLIDIFY")
    solidify.thickness = _SOLIDIFY_THICKNESS
    solidify.offset = 1.0
    bpy.ops.object.modifier_apply(modifier=solidify.name)

    if len(garment.data.vertices) < _SUBSURF_VERT_THRESHOLD:
        subsurf = garment.modifiers.new(name="GarmentDensity", type="SUBSURF")
        subsurf.levels = 1
        bpy.ops.object.modifier_apply(modifier=subsurf.name)


def _skin_garment(garment: bpy.types.Object, body_mesh: bpy.types.Object, armature: bpy.types.Object) -> None:
    bpy.context.view_layer.objects.active = garment
    garment.select_set(True)
    dt = garment.modifiers.new(name="WeightTransfer", type="DATA_TRANSFER")
    dt.object = body_mesh
    dt.use_vert_data = True
    dt.data_types_verts = {"VGROUP_WEIGHTS"}
    dt.vert_mapping = "POLYINTERP_NEAREST"
    dt.layers_select_src = "ALL"
    dt.layers_select_dst = "NAME"
    dt.mix_mode = "REPLACE"
    dt.mix_factor = 1.0
    bpy.ops.object.modifier_apply(modifier=dt.name)

    arm_mod = garment.modifiers.new(name="Armature", type="ARMATURE")
    arm_mod.object = armature
    arm_mod.use_vertex_groups = True


def _build_body_bvh(body_mesh: bpy.types.Object) -> BVHTree:
    bm = bmesh.new()
    bm.from_mesh(body_mesh.data)
    bm.transform(body_mesh.matrix_world)
    bvh = BVHTree.FromBMesh(bm)
    bm.free()
    return bvh


def _collision_fix(garment: bpy.types.Object, body_bvh: BVHTree) -> None:
    garment_matrix = garment.matrix_world.copy()
    garment_matrix_inv = garment_matrix.inverted()
    bm_garment = bmesh.new()
    bm_garment.from_mesh(garment.data)

    for v in bm_garment.verts:
        v_world = garment_matrix @ v.co
        nearest = body_bvh.find_nearest(v_world)
        if nearest is None:
            continue
        loc, normal, _index, _dist = nearest
        signed_dist = (v_world - loc).dot(normal)
        if signed_dist < _COLLISION_CLEARANCE:
            v_world = loc + normal * _COLLISION_CLEARANCE
            v.co = garment_matrix_inv @ v_world

    bm_garment.to_mesh(garment.data)
    bm_garment.free()


def _validate(garment_meshes: list[bpy.types.Object], armature: bpy.types.Object) -> None:
    bone_names = {b.name for b in armature.data.bones}
    for gm in garment_meshes:
        vg = gm.vertex_groups.get("VG_ANCHOR")
        has_anchors = any(any(g.group == vg.index for g in v.groups) for v in gm.data.vertices) if vg else False
        if vg is None or not has_anchors:
            raise RuntimeError(f"garment '{gm.name}' VG_ANCHOR is empty or missing after post-processing")
        arm_mods = [m for m in gm.modifiers if m.type == "ARMATURE" and m.object == armature]
        if not arm_mods:
            raise RuntimeError(f"garment '{gm.name}' missing ARMATURE modifier targeting body armature")
        orphan_groups = {vg.name for vg in gm.vertex_groups} - bone_names - {"VG_ANCHOR"}
        if orphan_groups:
            raise RuntimeError(f"garment '{gm.name}' has vertex groups with no matching bone: {orphan_groups}")


def _export_glb(output_path: str, assembly: dict) -> None:
    if assembly:
        bpy.context.scene["dsh:assembly"] = assembly
        for obj in bpy.context.scene.objects:
            if obj.type == "MESH":
                obj["dsh:assembly"] = assembly
    bpy.ops.export_scene.gltf(
        filepath=output_path,
        export_format="GLB",
        export_yup=True,
        export_materials="EXPORT",
        export_skins=True,
        export_morph=False,
        export_animations=False,
        export_extras=True,
        export_draco_mesh_compression_enable=False,
    )


def _render_preview(output_path: str) -> None:
    """渲染-比较-精化循环用的低质量 Cycles CPU 预览。无头容器里 EEVEE 不可靠（需要 OpenGL 上下文），Cycles CPU 通用。"""
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 16
    scene.cycles.device = "CPU"
    scene.render.resolution_x = 384
    scene.render.resolution_y = 576
    scene.render.film_transparent = False

    world = scene.world or bpy.data.worlds.new("World")
    scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg is None:
        bg = world.node_tree.nodes.new("ShaderNodeBackground")
    bg.inputs[0].default_value = (1.0, 1.0, 1.0, 1.0)
    bg.inputs[1].default_value = 1.0

    cam_data = bpy.data.cameras.new("PreviewCam")
    cam = bpy.data.objects.new("PreviewCam", cam_data)
    scene.collection.objects.link(cam)
    cam.location = (2.8, -2.8, 1.6)
    direction = -cam.location.normalized()
    cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    scene.camera = cam

    key_data = bpy.data.lights.new("KeyLight", type="AREA")
    key_data.energy = 800
    key_data.size = 2.0
    key = bpy.data.objects.new("KeyLight", key_data)
    key.location = (3.0, -3.0, 4.0)
    scene.collection.objects.link(key)

    fill_data = bpy.data.lights.new("FillLight", type="AREA")
    fill_data.energy = 300
    fill_data.size = 3.0
    fill = bpy.data.objects.new("FillLight", fill_data)
    fill.location = (-3.0, -1.0, 2.0)
    scene.collection.objects.link(fill)

    scene.render.filepath = output_path
    bpy.ops.render.render(write_still=True)


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    _reset_scene()

    armature, body_mesh, imported_objects = _import_body_glb(args.body_glb)
    ctx = _build_ctx(armature, body_mesh, args.socket)
    _build_garment(ctx)

    garment_meshes = [o for o in bpy.context.scene.objects if o.type == "MESH" and o not in imported_objects]
    if not garment_meshes:
        raise RuntimeError("LLM code created no garment meshes")

    if args.kind == "accessory":
        # 配件跳过 fit/solidify/skin/collision：它们是刚体网格，运行时挂在 socket 骨上（见 backend/README.md 已知限制）。渲染时保留身体以让 eval/refine 循环看到穿戴位置，再移除身体使导出只含配件几何。
        if args.render_output:
            _render_preview(args.render_output)
            print(f"[scaffold] preview rendered to {args.render_output}")
        for obj in imported_objects:
            if obj.name in bpy.data.objects:
                bpy.data.objects.remove(bpy.data.objects[obj.name], do_unlink=True)
    else:
        # 循环中身体不变，碰撞 BVH 只构建一次。
        body_bvh = _build_body_bvh(body_mesh)
        for gm in garment_meshes:
            _fit_garment(gm, body_mesh)
            _drape_garment(gm, body_mesh)
            _solidify_garment(gm)
            _skin_garment(gm, body_mesh, armature)
            _collision_fix(gm, body_bvh)

        _validate(garment_meshes, armature)

        if args.render_output:
            _render_preview(args.render_output)
            print(f"[scaffold] preview rendered to {args.render_output}")

        # 服装 GLB 只含服装蒙皮网格与身体 armature（MODEL_SPEC.md §4.1），导出前移除导入的身体网格。
        for obj in imported_objects:
            if obj.name in bpy.data.objects:
                live_obj = bpy.data.objects[obj.name]
                if live_obj.type != "ARMATURE":
                    bpy.data.objects.remove(live_obj, do_unlink=True)

    assembly = json.loads(args.assembly) if args.assembly else {}
    _export_glb(args.output, assembly)
    print(f"[scaffold] {args.kind} GLB exported to {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
