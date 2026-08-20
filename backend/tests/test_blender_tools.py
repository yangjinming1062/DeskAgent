import json
import struct

import pytest

from services.companion import blender_tools
from services.companion.blender_tools import _strip_code_fences
from services.companion.model_service import parse_glb_json
from services.companion.rig_bone_specs import (
    RIG_BONE_HIERARCHIES,
    bone_names,
    format_bone_tree,
    get_bone_hierarchy,
)


def _make_glb(gltf_dict: dict) -> bytes:
    json_bytes = json.dumps(gltf_dict).encode("utf-8")
    # 按 GLB 规范用空格补齐到 4 字节对齐。
    while len(json_bytes) % 4 != 0:
        json_bytes += b" "
    bin_data = b"\x00" * 4

    json_chunk = struct.pack("<II", len(json_bytes), 0x4E4F534A) + json_bytes
    bin_chunk = struct.pack("<II", len(bin_data), 0x004E4942) + bin_data

    total = 12 + len(json_chunk) + len(bin_chunk)
    header = struct.pack("<III", 0x46546C67, 2, total)
    return header + json_chunk + bin_chunk


class TestRigBoneSpecs:
    def test_all_seven_rig_types_present(self):
        assert set(RIG_BONE_HIERARCHIES.keys()) == {
            "biped",
            "quadruped",
            "avian",
            "serpentine",
            "aquatic",
            "hexapod",
            "octopod",
        }

    def test_biped_has_25_bones(self):
        bones = get_bone_hierarchy("biped")
        assert len(bones) == 25

    def test_get_bone_hierarchy_unknown_falls_back_to_biped(self):
        bones = get_bone_hierarchy("nonexistent")
        assert len(bones) == 25

    def test_format_bone_tree_renders_hierarchy(self):
        tree = format_bone_tree("biped")
        assert "Hips" in tree
        assert "Head" in tree
        assert "LeftArm" in tree

    def test_bone_names_returns_set(self):
        names = bone_names("quadruped")
        assert isinstance(names, set)
        assert "Hips" in names
        assert "Head" in names
        assert "Tail" in names


class TestStripCodeFences:
    def test_plain_code(self):
        assert _strip_code_fences("print('hello')") == "print('hello')"

    def test_python_fence(self):
        raw = "```python\nprint('hello')\n```"
        assert _strip_code_fences(raw) == "print('hello')"

    def test_bare_fence(self):
        raw = "```\nprint('hello')\n```"
        assert _strip_code_fences(raw) == "print('hello')"

    def test_short_language_tag(self):
        raw = "```py\nprint('hello')\n```"
        assert _strip_code_fences(raw) == "print('hello')"

    def test_uppercase_language_tag(self):
        raw = "```Python\nprint('hello')\n```"
        assert _strip_code_fences(raw) == "print('hello')"

    def test_no_opening_fence(self):
        assert _strip_code_fences("print('hello')") == "print('hello')"

    def test_unclosed_opening_fence(self):
        # LLM 输出了开围栏但没有收尾——剥除开围栏，保留正文。
        raw = "```python\nprint('hello')"
        assert _strip_code_fences(raw) == "print('hello')"

    def test_whitespace_trimmed(self):
        assert _strip_code_fences("  code  ") == "code"


class TestGlbValidation:
    def test_parse_valid_glb_json(self):
        glb = _make_glb({"nodes": [{"name": "Hips"}]})
        parsed = parse_glb_json(glb)
        assert parsed is not None
        assert parsed["nodes"][0]["name"] == "Hips"

    def test_parse_invalid_magic(self):
        bad = b"XXXX" + b"\x00" * 20
        assert parse_glb_json(bad) is None

    def test_parse_too_short(self):
        assert parse_glb_json(b"\x00" * 5) is None


class TestScaffoldMerge:
    _MARKER = "    __BUILD_BODY__"

    def _scaffold(self, tmp_path):
        path = tmp_path / "scaffold.py"
        path.write_text("def _build_body():\n    __BUILD_BODY__\n    return\n")
        return path

    def test_merge_replaces_marker(self, tmp_path):
        merged = blender_tools._merge_scaffold("bpy.ops.mesh.primitive_cube_add()", self._scaffold(tmp_path), self._MARKER)
        assert "primitive_cube_add" in merged
        assert "__BUILD_BODY__" not in merged

    def test_merge_indents_code(self, tmp_path):
        merged = blender_tools._merge_scaffold("if True:\n    pass", self._scaffold(tmp_path), self._MARKER)
        # 8 空格 = 4（marker 处函数体缩进）+ 4（源码 if 体缩进）。
        assert "        pass" in merged


class TestRunBlenderScaffold:
    @pytest.mark.asyncio
    async def test_missing_scaffold_fails_fast(self, tmp_path):
        result = await blender_tools.run_blender_scaffold(tmp_path / "nope.py", "pass", "M", [])
        assert result.success is False
        assert "scaffold not found" in result.stderr

    @pytest.mark.asyncio
    async def test_blender_not_found(self, monkeypatch, tmp_path):
        async def _raise(*a, **kw):
            raise FileNotFoundError

        (tmp_path / "scaffold.py").write_text("x = 1")
        monkeypatch.setattr(blender_tools, "run_blender", _raise)
        result = await blender_tools.run_blender_scaffold(tmp_path / "scaffold.py", "pass", "M", [], io_dir=tmp_path)
        assert result.success is False
        assert "not found" in result.stderr

    @pytest.mark.asyncio
    async def test_successful_execution(self, monkeypatch, tmp_path):
        (tmp_path / "scaffold.py").write_text("def _build_body():\n    __BUILD_BODY__\n")
        valid_glb = _make_glb({"nodes": [{"name": "Hips"}]})

        async def _fake_run_blender(io_dir, script_name, payload, *, timeout, name_hint="adhoc"):
            (tmp_path / "output.glb").write_bytes(valid_glb)
            (tmp_path / "preview.png").write_bytes(b"\x89PNG fake")
            return 0, ""

        monkeypatch.setattr(blender_tools, "run_blender", _fake_run_blender)

        result = await blender_tools.run_blender_scaffold(
            tmp_path / "scaffold.py", "pass", "    __BUILD_BODY__", ["--seed-front", "f.jpg"], io_dir=tmp_path
        )
        assert result.success is True
        assert result.glb_bytes == valid_glb
        assert result.preview_png is not None
