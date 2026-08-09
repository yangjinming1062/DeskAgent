import argparse
import os
import sys
from typing import Iterable

import bpy
from mathutils import Vector

ARKIT_BLENDSHAPES: tuple[str, ...] = (
    "eyeBlinkLeft",
    "eyeBlinkRight",
    "eyeWideLeft",
    "eyeWideRight",
    "eyeSquintLeft",
    "eyeSquintRight",
    "eyesLookDown",
    "browInnerUp",
    "browInnerDown",
    "jawOpen",
    "mouthSmile",
    "mouthSmileRight",
    "mouthFrown",
    "cheekSquintLeft",
    "noseSneerLeft",
    "tongueOut",
    "eyeCloseTight",
    "eyeDroopLeft",
    "eyeDroopRight",
    "eyeWidenFear",
    "eyeNarrow",
    "browFurrow",
    "browOuterUp",
    "nostrilFlare",
    "mouthTremble",
    "mouthCornerDown",
    "jawClench",
    "lipPress",
    "faceWince",
    "cheekPuff",
    "eyeCloseLeft",
    "eyeCloseRight",
    "browRaiseLeft",
    "browRaiseRight",
    "mouthPucker",
    "lipBiteLower",
    "noseWrinkle",
    "cheekBlush",
    "Body_Height",
    "Body_Weight",
    "Body_Muscle",
    "Body_Shoulders",
    "Face_Width",
    "Face_Jaw",
)


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


def _import_glb(path: str) -> bpy.types.Object:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".glb":
        bpy.ops.import_scene.gltf(filepath=path)
    elif ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=path)
    else:
        raise ValueError(f"unsupported input extension: {ext}")
    armatures = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not meshes:
        raise RuntimeError(f"no mesh in {path}")
    return meshes[0], armatures[0] if armatures else None


def _body_region(mesh: bpy.types.Object, bone_name: str = "Head") -> dict[str, Vector]:
    """Returns bounding-box centroids per ARKit region, anchored on the Head bone."""
    head_bone = None
    if mesh.parent and mesh.parent.type == "ARMATURE":
        head_bone = mesh.parent.data.bones.get(bone_name)
    fallback = Vector((0.0, 1.6, 0.15)) if not head_bone else head_bone.head_local
    bounds = [Vector(c) for c in mesh.bound_box]
    cx = sum(b.x for b in bounds) / 8.0
    cy = sum(b.y for b in bounds) / 8.0
    cz = sum(b.z for b in bounds) / 8.0
    head_y = fallback.y + 0.05
    return {
        "left_eye": Vector((fallback.x - 0.035, head_y, fallback.z + 0.04)),
        "right_eye": Vector((fallback.x + 0.035, head_y, fallback.z + 0.04)),
        "brow_left": Vector((fallback.x - 0.04, head_y + 0.04, fallback.z + 0.06)),
        "brow_right": Vector((fallback.x + 0.04, head_y + 0.04, fallback.z + 0.06)),
        "jaw": Vector((fallback.x, head_y - 0.09, fallback.z + 0.045)),
        "mouth_left": Vector((fallback.x - 0.025, head_y - 0.075, fallback.z + 0.05)),
        "mouth_right": Vector((fallback.x + 0.025, head_y - 0.075, fallback.z + 0.05)),
        "cheek_left": Vector((fallback.x - 0.055, head_y - 0.02, fallback.z + 0.035)),
        "cheek_right": Vector((fallback.x + 0.055, head_y - 0.02, fallback.z + 0.035)),
        "nose": Vector((fallback.x, head_y - 0.02, fallback.z + 0.07)),
        "forehead": Vector((fallback.x, head_y + 0.07, fallback.z + 0.05)),
        "body_center": Vector((cx, cy * 0.5, cz)),
    }


def _add_shape_keys(mesh: bpy.types.Object) -> None:
    mesh.shape_key_add(name="Basis", from_mix=False)
    mesh.data.shape_keys.use_relative = True


def _add_shape(mesh: bpy.types.Object, name: str, regions: dict[str, Vector], deformer) -> None:
    sk = mesh.shape_key_add(name=name, from_mix=False)
    deformer(sk.data, regions)


def _displace_vertices(coords, region: Vector, direction: Vector, radius: float, magnitude: float) -> None:
    for v in coords:
        d = (v.co - region).length
        if d > radius:
            continue
        falloff = max(0.0, 1.0 - d / radius)
        v.co += direction * (magnitude * falloff)


_DISPLACEMENTS: dict[str, callable] = {
    "eyeBlinkLeft": lambda d, r: _displace_vertices(d, r["left_eye"], Vector((0, -1, 0)), 0.04, 0.025),
    "eyeBlinkRight": lambda d, r: _displace_vertices(d, r["right_eye"], Vector((0, -1, 0)), 0.04, 0.025),
    "eyeWideLeft": lambda d, r: _displace_vertices(d, r["left_eye"], Vector((0, 1, 0)), 0.04, 0.015),
    "eyeWideRight": lambda d, r: _displace_vertices(d, r["right_eye"], Vector((0, 1, 0)), 0.04, 0.015),
    "eyeSquintLeft": lambda d, r: _displace_vertices(d, r["left_eye"], Vector((0, -1, 0)), 0.04, 0.01),
    "eyeSquintRight": lambda d, r: _displace_vertices(d, r["right_eye"], Vector((0, -1, 0)), 0.04, 0.01),
    "eyesLookDown": lambda d, r: (_displace_vertices(d, r["left_eye"], Vector((0, -1, 0)), 0.05, 0.005), _displace_vertices(d, r["right_eye"], Vector((0, -1, 0)), 0.05, 0.005)),
    "browInnerUp": lambda d, r: _displace_vertices(d, r["brow_left"], Vector((0, 0, 0.5)), 0.06, 0.015),
    "browInnerDown": lambda d, r: _displace_vertices(d, r["brow_left"], Vector((0, -1, 0)), 0.06, 0.01),
    "jawOpen": lambda d, r: _displace_vertices(d, r["jaw"], Vector((0, -1, 0)), 0.05, 0.03),
    "mouthSmile": lambda d, r: (
        _displace_vertices(d, r["mouth_left"], Vector((0, 1, 0.3)), 0.05, 0.012),
        _displace_vertices(d, r["mouth_right"], Vector((0, 1, 0.3)), 0.05, 0.012),
    ),
    "mouthSmileRight": lambda d, r: _displace_vertices(d, r["mouth_right"], Vector((0, 1, 0.3)), 0.05, 0.015),
    "mouthFrown": lambda d, r: (_displace_vertices(d, r["mouth_left"], Vector((0, -1, 0)), 0.05, 0.01), _displace_vertices(d, r["mouth_right"], Vector((0, -1, 0)), 0.05, 0.01)),
    "cheekSquintLeft": lambda d, r: _displace_vertices(d, r["cheek_left"], Vector((1, 1, 0)), 0.05, 0.008),
    "noseSneerLeft": lambda d, r: _displace_vertices(d, r["nose"], Vector((0, 1, 0)), 0.04, 0.012),
    "tongueOut": lambda d, r: _displace_vertices(d, r["jaw"], Vector((0, 0, 1)), 0.03, 0.025),
    "eyeCloseTight": lambda d, r: (_displace_vertices(d, r["left_eye"], Vector((0, -1, 0)), 0.05, 0.03), _displace_vertices(d, r["right_eye"], Vector((0, -1, 0)), 0.05, 0.03)),
    "eyeDroopLeft": lambda d, r: _displace_vertices(d, r["left_eye"], Vector((0, -1, 0.3)), 0.05, 0.008),
    "eyeDroopRight": lambda d, r: _displace_vertices(d, r["right_eye"], Vector((0, -1, 0.3)), 0.05, 0.008),
    "eyeWidenFear": lambda d, r: (_displace_vertices(d, r["left_eye"], Vector((0, 1, 0)), 0.05, 0.02), _displace_vertices(d, r["right_eye"], Vector((0, 1, 0)), 0.05, 0.02)),
    "eyeNarrow": lambda d, r: (_displace_vertices(d, r["left_eye"], Vector((0, -1, 0)), 0.05, 0.012), _displace_vertices(d, r["right_eye"], Vector((0, -1, 0)), 0.05, 0.012)),
    "browFurrow": lambda d, r: _displace_vertices(d, r["brow_left"], Vector((-1, 1, 0)), 0.06, 0.012),
    "browOuterUp": lambda d, r: _displace_vertices(d, r["brow_right"], Vector((1, 1, 0)), 0.06, 0.015),
    "nostrilFlare": lambda d, r: _displace_vertices(d, r["nose"], Vector((0, 0, 1)), 0.025, 0.01),
    "mouthTremble": lambda d, r: _displace_vertices(d, r["mouth_left"], Vector((0, -0.5, 0)), 0.03, 0.005),
    "mouthCornerDown": lambda d, r: (
        _displace_vertices(d, r["mouth_left"], Vector((0, -1, 0)), 0.03, 0.012),
        _displace_vertices(d, r["mouth_right"], Vector((0, -1, 0)), 0.03, 0.012),
    ),
    "jawClench": lambda d, r: _displace_vertices(d, r["jaw"], Vector((0, 1, 0)), 0.05, 0.008),
    "lipPress": lambda d, r: (_displace_vertices(d, r["mouth_left"], Vector((0, 0, -1)), 0.03, 0.008), _displace_vertices(d, r["mouth_right"], Vector((0, 0, -1)), 0.03, 0.008)),
    "faceWince": lambda d, r: _displace_vertices(d, r["nose"], Vector((0, -1, 0)), 0.06, 0.01),
    "cheekPuff": lambda d, r: (_displace_vertices(d, r["cheek_left"], Vector((1, 0, 0)), 0.04, 0.015), _displace_vertices(d, r["cheek_right"], Vector((-1, 0, 0)), 0.04, 0.015)),
    "eyeCloseLeft": lambda d, r: _displace_vertices(d, r["left_eye"], Vector((0, -1, 0)), 0.04, 0.02),
    "eyeCloseRight": lambda d, r: _displace_vertices(d, r["right_eye"], Vector((0, -1, 0)), 0.04, 0.02),
    "browRaiseLeft": lambda d, r: _displace_vertices(d, r["brow_left"], Vector((0, 1, 0)), 0.06, 0.015),
    "browRaiseRight": lambda d, r: _displace_vertices(d, r["brow_right"], Vector((0, 1, 0)), 0.06, 0.015),
    "mouthPucker": lambda d, r: (_displace_vertices(d, r["mouth_left"], Vector((1, 0, 1)), 0.03, 0.01), _displace_vertices(d, r["mouth_right"], Vector((-1, 0, 1)), 0.03, 0.01)),
    "lipBiteLower": lambda d, r: _displace_vertices(d, r["mouth_left"], Vector((0, -1, 0.5)), 0.03, 0.008),
    "noseWrinkle": lambda d, r: _displace_vertices(d, r["nose"], Vector((0, 1, 0)), 0.04, 0.008),
    "cheekBlush": lambda d, r: (
        _displace_vertices(d, r["cheek_left"], Vector((1, 0, 0.5)), 0.04, 0.003),
        _displace_vertices(d, r["cheek_right"], Vector((-1, 0, 0.5)), 0.04, 0.003),
    ),
    "Body_Height": lambda d, r: _displace_vertices(d, r["body_center"], Vector((0, 1, 0)), 1.0, 0.1),
    "Body_Weight": lambda d, r: _displace_vertices(d, r["body_center"], Vector((0.7, 0, 0.7)), 1.0, 0.04),
    "Body_Muscle": lambda d, r: _displace_vertices(d, r["body_center"], Vector((1, 0, 1)), 1.0, 0.02),
    "Body_Shoulders": lambda d, r: _displace_vertices(d, r["body_center"], Vector((1, 0, 0)), 1.0, 0.03),
    "Face_Width": lambda d, r: _displace_vertices(d, r["cheek_left"], Vector((-1, 0, 0)), 0.08, 0.012),
    "Face_Jaw": lambda d, r: _displace_vertices(d, r["jaw"], Vector((0, -1, 0)), 0.06, 0.008),
}


def inject(input_glb: str, output_glb: str) -> list[str]:
    _reset_scene()
    mesh, _arm = _import_glb(input_glb)
    regions = _body_region(mesh)
    _add_shape_keys(mesh)
    added: list[str] = []
    for name in ARKIT_BLENDSHAPES:
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
        export_morph_normal=True,
        export_morph_tangent=False,
        export_animations=True,
        export_animation_mode="ACTIONS",
        export_morph_animation=False,
    )
    return added


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    if not os.path.isfile(args.input):
        print(f"input not found: {args.input}", file=sys.stderr)
        return 2
    os.makedirs(os.path.dirname(os.path.abspath(args.output)) or ".", exist_ok=True)
    added = inject(args.input, args.output)
    print(f"injected {len(added)}/{len(ARKIT_BLENDSHAPES)} morph targets: {', '.join(added[:8])}, ...")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
