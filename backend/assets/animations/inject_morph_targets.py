import argparse
import math
import os
import sys

import bpy
from mathutils import Matrix, Vector

# 仅注入运行时真正消费的 morph：单眼眨眼、TTS 张嘴，以及 morph_params 控制的 6 项身材参数；表情面挪到聊天半身像生成上——每个未用到的表情 shape 都会让导出 GLB 多一个完整顶点缓冲。
BLENDSHAPES: tuple[str, ...] = ("eyeBlinkLeft", "eyeBlinkRight", "jawOpen", "Body_Height", "Body_Weight", "Body_Muscle", "Body_Shoulders", "Face_Width", "Face_Jaw")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    argv = argv[argv.index("--") + 1 :] if "--" in argv else []
    p = argparse.ArgumentParser(prog="inject_morph_targets")
    p.add_argument("--input", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--landmarks", choices=("auto",), default="auto")
    return p.parse_args(argv)


def _reset_scene() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)


def _import_glb(path: str) -> tuple[bpy.types.Object, bpy.types.Object | None]:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".glb":
        bpy.ops.import_scene.gltf(filepath=path)
    elif ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=path)
    else:
        raise ValueError(f"unsupported input extension: {ext}")

    armatures = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
    armature = armatures[0] if armatures else None

    character_mesh = None
    for o in bpy.context.scene.objects:
        if (
            o.type == "MESH"
            and o.name != "Icosphere"
            and (o.parent == armature or any(m.type == "ARMATURE" for m in o.modifiers) or (o.data.materials and len(o.data.materials) > 0))
        ):
            character_mesh = o
            break
    if not character_mesh:
        character_mesh = next((o for o in bpy.context.scene.objects if o.type == "MESH"), None)

    if not character_mesh:
        raise RuntimeError(f"no mesh in {path}")

    for o in list(bpy.context.scene.objects):
        if o.type == "MESH" and o != character_mesh:
            bpy.data.objects.remove(o, do_unlink=True)

    # 朝向校正：Tripo 模型若沿 Y 而非 X 展臂（侧向建模），绕 Z 旋转 -90°
    if armature:
        left_hand = next((b for b in armature.data.bones if "lefthand" in b.name.lower() or "left_hand" in b.name.lower()), None)
        if left_hand and abs(left_hand.head_local.y) > abs(left_hand.head_local.x):
            rot_mat = Matrix.Rotation(math.radians(-90.0), 4, "Z")
            armature.matrix_world = rot_mat @ armature.matrix_world
            bpy.context.view_layer.objects.active = armature
            bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)

    if armature:
        for b in armature.data.bones:
            orig = b.name
            if orig.startswith("mixamorig:"):
                b.name = orig[len("mixamorig:") :]
            elif orig.startswith("mixamorig_"):
                b.name = orig[len("mixamorig_") :]

    if character_mesh:
        for vg in character_mesh.vertex_groups:
            orig = vg.name
            if orig.startswith("mixamorig:"):
                vg.name = orig[len("mixamorig:") :]
            elif orig.startswith("mixamorig_"):
                vg.name = orig[len("mixamorig_") :]

    return character_mesh, armature


def _body_region(mesh: bpy.types.Object, armature: bpy.types.Object | None = None) -> dict[str, Vector]:
    """返回各 ARKit 区域的包围盒中心（锚定 Head 骨）。"""
    head_bone = None
    if armature and armature.data.bones:
        for b in armature.data.bones:
            if b.name.lower() in ("head", "mixamorig:head") or b.name.endswith(":Head") or b.name.endswith("_Head"):
                head_bone = b
                break

    fallback = Vector((0.0, 0.0, 0.8)) if not head_bone else head_bone.head_local
    bounds = [Vector(c) for c in mesh.bound_box]
    cx = sum(b.x for b in bounds) / 8.0
    cy = sum(b.y for b in bounds) / 8.0
    cz = sum(b.z for b in bounds) / 8.0

    hx, hy, hz = fallback.x, fallback.y, fallback.z
    return {
        "left_eye": Vector((hx + 0.03, hy - 0.04, hz + 0.02)),
        "right_eye": Vector((hx - 0.03, hy - 0.04, hz + 0.02)),
        "jaw": Vector((hx, hy - 0.03, hz - 0.07)),
        "cheek_left": Vector((hx + 0.045, hy - 0.03, hz - 0.02)),
        "cheek_right": Vector((hx - 0.045, hy - 0.03, hz - 0.02)),
        "body_center": Vector((cx, cy, cz)),
    }


def _add_shape_keys(mesh: bpy.types.Object) -> None:
    mesh.shape_key_add(name="Basis", from_mix=False)
    mesh.data.shape_keys.use_relative = True


def _add_shape(mesh: bpy.types.Object, name: str, regions: dict[str, Vector], deformer) -> None:
    sk = mesh.shape_key_add(name=name, from_mix=False)
    sk.value = 0.0
    deformer(sk.data, regions)


def _displace_vertices(coords, region: Vector, direction: Vector, radius: float, magnitude: float) -> None:
    for v in coords:
        d = (v.co - region).length
        if d > radius:
            continue
        falloff = max(0.0, 1.0 - d / radius)
        v.co += direction * (magnitude * falloff)


_DISPLACEMENTS: dict[str, callable] = {
    "eyeBlinkLeft": lambda d, r: _displace_vertices(d, r["left_eye"], Vector((0, 0, -1)), 0.03, 0.015),
    "eyeBlinkRight": lambda d, r: _displace_vertices(d, r["right_eye"], Vector((0, 0, -1)), 0.03, 0.015),
    "jawOpen": lambda d, r: _displace_vertices(d, r["jaw"], Vector((0, 0, -1)), 0.05, 0.025),
    "Body_Height": lambda d, r: _displace_vertices(d, r["body_center"], Vector((0, 0, 1)), 1.0, 0.08),
    "Body_Weight": lambda d, r: _displace_vertices(d, r["body_center"], Vector((1, 1, 0)), 1.0, 0.03),
    "Body_Muscle": lambda d, r: _displace_vertices(d, r["body_center"], Vector((1, 1, 0)), 1.0, 0.015),
    "Body_Shoulders": lambda d, r: _displace_vertices(d, r["body_center"], Vector((1, 0, 0)), 1.0, 0.025),
    "Face_Width": lambda d, r: _displace_vertices(d, r["cheek_left"], Vector((1, 0, 0)), 0.06, 0.01),
    "Face_Jaw": lambda d, r: _displace_vertices(d, r["jaw"], Vector((0, 0, -1)), 0.05, 0.006),
}


def inject(input_glb: str, output_glb: str) -> list[str]:
    _reset_scene()
    mesh, armature = _import_glb(input_glb)
    regions = _body_region(mesh, armature)
    _add_shape_keys(mesh)
    added: list[str] = []
    for name in BLENDSHAPES:
        deformer = _DISPLACEMENTS.get(name)
        if not deformer:
            continue
        _add_shape(mesh, name, regions, deformer)
        added.append(name)

    bpy.ops.export_scene.gltf(
        filepath=output_glb,
        export_format="GLB",
        export_yup=True,
        export_materials="EXPORT",
        export_skins=True,
        export_morph=True,
        export_morph_normal=False,
        export_normals=False,
        export_morph_tangent=False,
        export_animations=True,
        export_animation_mode="ACTIONS",
        export_morph_animation=False,
        export_draco_mesh_compression_enable=False,
    )
    return added


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    if not os.path.isfile(args.input):
        print(f"input not found: {args.input}", file=sys.stderr)
        return 2
    os.makedirs(os.path.dirname(os.path.abspath(args.output)) or ".", exist_ok=True)
    added = inject(args.input, args.output)
    print(f"injected {len(added)}/{len(BLENDSHAPES)} morph targets: {', '.join(added[:8])}, ...")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
