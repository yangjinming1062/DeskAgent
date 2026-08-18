"""Render four horizontal view snapshots of a GLB for vision-LLM face
detection (feeds ``rig_orientation.detect_face_yaw``). View names map to the
face direction they reveal: front→-Y, right→+X, back→+Y, left→-X (Blender
world axes, canonical front = -Y).

    blender --background --python render_face_views.py -- \
        --input in.glb --outdir dir
"""

import argparse
import sys
from pathlib import Path

import bpy
from mathutils import Vector

_VIEWS: dict[str, tuple[float, float]] = {"front": (0.0, -1.0), "right": (1.0, 0.0), "back": (0.0, 1.0), "left": (-1.0, 0.0)}
_RES: int = 256


def _parse_args(argv: list[str]) -> argparse.Namespace:
    argv = argv[argv.index("--") + 1 :] if "--" in argv else []
    p = argparse.ArgumentParser(prog="render_face_views")
    p.add_argument("--input", required=True)
    p.add_argument("--outdir", required=True)
    return p.parse_args(argv)


def main() -> int:
    args = _parse_args(sys.argv)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=args.input)
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not meshes:
        print("render_face_views: imported GLB contains no mesh", file=sys.stderr)
        return 1
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.shade_smooth()

    pts = [m.matrix_world @ Vector(c) for m in meshes for c in m.bound_box]
    center = Vector(tuple(sum(p[i] for p in pts) / len(pts) for i in range(3)))
    radius = max((max(p.x for p in pts) - min(p.x for p in pts), max(p.y for p in pts) - min(p.y for p in pts), max(p.z for p in pts) - min(p.z for p in pts)))

    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    cam.data.lens = 50
    bpy.context.scene.collection.objects.link(cam)
    bpy.context.scene.camera = cam
    light = bpy.data.objects.new("sun", bpy.data.lights.new("sun", "SUN"))
    light.location = (center.x + 2, center.y - 3, center.z + 4)
    light.data.energy = 3
    bpy.context.scene.collection.objects.link(light)
    bpy.context.scene.render.resolution_x = _RES
    bpy.context.scene.render.resolution_y = _RES
    bpy.context.scene.world = bpy.data.worlds.new("w")
    bpy.context.scene.world.node_tree.nodes["Background"].inputs[0].default_value = (0.9, 0.9, 0.92, 1)

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    dist = radius * 2.2
    for view, (dx, dy) in _VIEWS.items():
        cam.location = (center.x + dx * dist, center.y + dy * dist, center.z)
        direction = center - cam.location
        cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
        bpy.context.scene.render.filepath = str(outdir / f"{view}.png")
        bpy.ops.render.render(write_still=True)
    print(f"render_face_views: wrote 4 views to {outdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
