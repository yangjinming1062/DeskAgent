import shutil

import pytest

from services.companion import model_service
from services.companion.rig_bone_specs import RIG_BONE_HIERARCHIES, bone_names
from services.companion.rig_layout import layout_skeleton

_ALL_RIG_TYPES = sorted(RIG_BONE_HIERARCHIES)


class TestLayoutSkeleton:
    @pytest.mark.parametrize("rig_type", _ALL_RIG_TYPES)
    def test_covers_every_bone_of_the_spec(self, rig_type):
        assert set(layout_skeleton(rig_type)) == bone_names(rig_type)

    @pytest.mark.parametrize("rig_type", _ALL_RIG_TYPES)
    def test_parents_match_the_spec(self, rig_type):
        layout = layout_skeleton(rig_type)
        for name, parent, _ in RIG_BONE_HIERARCHIES[rig_type]:
            assert layout[name].parent == parent

    @pytest.mark.parametrize("rig_type", _ALL_RIG_TYPES)
    def test_bones_stay_within_sane_box(self, rig_type):
        for bone in layout_skeleton(rig_type).values():
            for point in (bone.head, bone.tail):
                assert -0.8 <= point[0] <= 0.8, f"x out of box: {point}"
                assert 0.0 <= point[1] <= 1.1, f"y out of box: {point}"
                assert -0.8 <= point[2] <= 0.8, f"z out of box: {point}"

    @pytest.mark.parametrize("rig_type", _ALL_RIG_TYPES)
    def test_child_attaches_at_parent_height(self, rig_type):
        # 横向(x)偏移与环形附着(触手/鳍沿 z 铺开)允许，仅垂直方向必须对齐父骨。
        layout = layout_skeleton(rig_type)
        for name, bone in layout.items():
            if bone.parent is None:
                continue
            parent = layout[bone.parent]
            assert bone.head[1] in (parent.head[1], parent.tail[1]), f"{name} detached from {bone.parent}"

    @pytest.mark.parametrize("rig_type", _ALL_RIG_TYPES)
    def test_no_zero_length_bones(self, rig_type):
        for name, bone in layout_skeleton(rig_type).items():
            assert bone.head != bone.tail, f"{name} has zero length"

    def test_unknown_rig_type_falls_back_to_biped(self):
        assert set(layout_skeleton("dragon")) == bone_names("biped")


@pytest.mark.asyncio
async def test_auto_rig_failure_fails_generation(monkeypatch, tmp_path):
    """auto-rig 非尽力式：Blender 失败必须抛错，绝不允许 ``has_rig=True`` 但实际未绑骨的模型上线。"""

    async def _boom(io_dir, script, args, *, timeout, name_hint="adhoc"):
        return 1, "blender exploded"

    monkeypatch.setattr(model_service, "run_blender", _boom)
    with pytest.raises(model_service.ModelGenerationError, match="自动绑骨失败"):
        await model_service._auto_rig_with_blender(b"\x00" * 20, "biped", io_dir=tmp_path)


_HAS_BLENDER = shutil.which("blender") is not None

_MAKE_CUBE_SCRIPT = """\
import bpy, sys
argv = sys.argv[sys.argv.index("--") + 1:]
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.mesh.primitive_uv_sphere_add(radius=0.5, location=(0, 0.5, 0))
bpy.ops.export_scene.gltf(filepath=argv[0], export_format="GLB")
"""


@pytest.mark.skipif(not _HAS_BLENDER, reason="blender binary not on PATH")
@pytest.mark.asyncio
async def test_auto_rig_blender_roundtrip(tmp_path, monkeypatch):
    """球体进入 → 绑骨 GLB 出来，再注入 morph 仍保留骨架；守护 rig→morph 的顺序假设（权重在导出后不丢）。"""
    gen_script = tmp_path / "make_cube.py"
    gen_script.write_text(_MAKE_CUBE_SCRIPT)
    bare_glb = tmp_path / "sphere.glb"
    await model_service.run_blender(tmp_path, "make_cube.py", [str(bare_glb)], timeout=120)
    assert bare_glb.exists(), "sphere GLB generation failed"

    rigged = await model_service._auto_rig_with_blender(bare_glb.read_bytes(), "biped", io_dir=tmp_path)
    gltf = model_service.parse_glb_json(rigged)
    assert gltf is not None, "rigged GLB unparseable"
    joints = {gltf["nodes"][i].get("name", "") for skin in gltf.get("skins", []) for i in skin.get("joints", [])}
    assert bone_names("biped") <= joints, "armature bones missing from exported GLB"

    final = await model_service._inject_morph_targets(rigged, io_dir=tmp_path)
    final_gltf = model_service.parse_glb_json(final)
    assert final_gltf is not None
    final_joints = {final_gltf["nodes"][i].get("name", "") for skin in final_gltf.get("skins", []) for i in skin.get("joints", [])}
    assert bone_names("biped") <= final_joints, "morph injection dropped the armature"
    final_weights = final_gltf.get("meshes", [{}])[0].get("weights", [])
    assert final_weights and not any(final_weights), "morph injection exported active default morph weights"
