"""Scaffold for LLM-generated Blender character scripts.

The orchestrator (``blender_llm_pipeline._execute_blender_script``) reads this
file, replaces the ``__BUILD_BODY__`` marker with LLM-generated Python code,
and writes the merged script to a temp file which is then executed via::

    blender --background --python <merged.py> -- \
        --output model.glb --render-output preview.png \
        --seed-front front.jpg --seed-right right.jpg --seed-back back.jpg

The LLM code runs inside the ``build_character(ctx)`` scope where *ctx*
exposes seed-image paths.  The scaffold handles argparse, scene reset, GLB
export and optional preview rendering so the LLM can focus purely on mesh /
armature / material creation.
"""

import argparse
import json
import sys

import bpy


# ─── Placeholder replaced by orchestrator ───────────────────────
# The LLM's code is string-substituted here before execution.  It has access
# to ``ctx`` (dict with seed image paths) and the full ``bpy`` module.
def _build_body(ctx: dict) -> None:  # pragma: no cover  -- replaced at runtime
    __BUILD_BODY__  # noqa: F821 -- placeholder marker


def _parse_args(argv: list[str]) -> argparse.Namespace:
    argv = argv[argv.index("--") + 1 :] if "--" in argv else argv
    p = argparse.ArgumentParser(prog="llm_bpy_scaffold")
    p.add_argument("--output", required=True)
    p.add_argument("--render-output", default="")
    p.add_argument("--seed-front", default="")
    p.add_argument("--seed-right", default="")
    p.add_argument("--seed-back", default="")
    p.add_argument("--params", default="")
    return p.parse_args(argv)


def _reset_scene() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)


def _export_glb(output_path: str) -> None:
    bpy.ops.export_scene.gltf(
        filepath=output_path,
        export_format="GLB",
        export_yup=True,
        export_materials="EXPORT",
        export_skins=True,
        export_morph=False,
        export_animations=False,
        export_draco_mesh_compression_enable=True,
        export_draco_mesh_compression_level=6,
        export_draco_position_quantization=14,
        export_draco_normal_quantization=10,
        export_draco_texcoord_quantization=12,
        export_draco_color_quantization=10,
        export_draco_generic_quantization=12,
    )


def _render_preview(output_path: str) -> None:
    """Low-quality Cycles CPU render for the render-compare-refine loop.

    EEVEE is unreliable in headless containers (needs an OpenGL context);
    Cycles CPU works everywhere.  16 samples at 384×576 is fast enough for
    the LLM to judge shape / proportion / colour.
    """
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

    ctx = {"seed_front": args.seed_front, "seed_right": args.seed_right, "seed_back": args.seed_back, "params": json.loads(args.params) if args.params else {}}
    _build_body(ctx)

    _export_glb(args.output)
    print(f"[scaffold] GLB exported to {args.output}")

    if args.render_output:
        _render_preview(args.render_output)
        print(f"[scaffold] preview rendered to {args.render_output}")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
