import json
import struct
from unittest.mock import AsyncMock, MagicMock

import pytest

from components import SETTINGS
from services.companion import blender_llm_pipeline
from services.companion.blender_llm_pipeline import (
    BlenderResult,
    EvaluationResult,
    _strip_code_fences,
    _validate_glb,
)
from services.companion.model_service import (
    _is_credits_exhausted_error,
    _parse_glb_json,
    _should_use_blender_fallback,
)
from services.companion.rig_bone_specs import RIG_BONE_HIERARCHIES, bone_names, format_bone_tree, get_bone_hierarchy


def _make_glb(gltf_dict: dict) -> bytes:
    json_bytes = json.dumps(gltf_dict).encode("utf-8")
    # Pad to 4-byte alignment with spaces (GLB spec requirement).
    while len(json_bytes) % 4 != 0:
        json_bytes += b" "
    bin_data = b"\x00" * 4

    json_chunk = struct.pack("<II", len(json_bytes), 0x4E4F534A) + json_bytes
    bin_chunk = struct.pack("<II", len(bin_data), 0x004E4942) + bin_data

    total = 12 + len(json_chunk) + len(bin_chunk)
    header = struct.pack("<III", 0x46546C67, 2, total)
    return header + json_chunk + bin_chunk


def _make_valid_biped_glb() -> bytes:
    required = sorted(bone_names("biped"))
    nodes = [{"name": name} for name in required]
    return _make_glb({"nodes": nodes, "skins": [{"joints": list(range(len(nodes)))}]})


class _FakeSession:
    """No-op context manager that returns a MagicMock — replaces ``SESSION_LOCAL``
    so pipeline integration tests don't need a real DB connection."""

    def __enter__(self):
        return MagicMock()

    def __exit__(self, *a):
        return False


def _fake_resolve_seeds(_filenames: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    return (
        {"front": "data:image/jpeg;base64,AAA", "right": "data:image/jpeg;base64,BBB", "back": "data:image/jpeg;base64,CCC"},
        {"front": "/tmp/f.jpg", "right": "/tmp/r.jpg", "back": "/tmp/b.jpg"},
    )


class TestRigBoneSpecs:
    def test_all_seven_rig_types_present(self):
        assert set(RIG_BONE_HIERARCHIES.keys()) == {"biped", "quadruped", "avian", "serpentine", "aquatic", "hexapod", "octopod"}

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


class TestShouldUseBlenderFallback:
    def test_disabled_returns_false(self, monkeypatch):
        monkeypatch.setattr(SETTINGS, "blender_llm_enabled", False)
        assert _should_use_blender_fallback(None) is False

    def test_explicit_blender_override(self, monkeypatch):
        monkeypatch.setattr(SETTINGS, "blender_llm_enabled", True)
        assert _should_use_blender_fallback("blender_llm") is True

    def test_explicit_tripo_override(self, monkeypatch):
        monkeypatch.setattr(SETTINGS, "blender_llm_enabled", True)
        monkeypatch.setattr(SETTINGS, "tripo_api_key", "some_key")
        assert _should_use_blender_fallback("tripo") is False

    def test_auto_detect_missing_key(self, monkeypatch):
        monkeypatch.setattr(SETTINGS, "blender_llm_enabled", True)
        monkeypatch.setattr(SETTINGS, "tripo_api_key", "")
        assert _should_use_blender_fallback(None) is True

    def test_auto_detect_with_key(self, monkeypatch):
        monkeypatch.setattr(SETTINGS, "blender_llm_enabled", True)
        monkeypatch.setattr(SETTINGS, "tripo_api_key", "tsk_xxx")
        assert _should_use_blender_fallback(None) is False

    def test_disabled_overrides_explicit_blender(self, monkeypatch):
        monkeypatch.setattr(SETTINGS, "blender_llm_enabled", False)
        assert _should_use_blender_fallback("blender_llm") is False


class TestIsCreditsExhaustedError:
    def test_insufficient_credit(self):
        exc = RuntimeError("insufficient credit balance")
        assert _is_credits_exhausted_error(exc) is True

    def test_quota_exceeded(self):
        exc = RuntimeError("quota exceeded")
        assert _is_credits_exhausted_error(exc) is True

    def test_billing_error(self):
        exc = RuntimeError("billing limit reached")
        assert _is_credits_exhausted_error(exc) is True

    def test_unrelated_error(self):
        exc = RuntimeError("network timeout")
        assert _is_credits_exhausted_error(exc) is False

    def test_empty_message(self):
        exc = RuntimeError("")
        assert _is_credits_exhausted_error(exc) is False


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
        # LLM emitted opening fence but no closing — strip opening, keep body.
        raw = "```python\nprint('hello')"
        assert _strip_code_fences(raw) == "print('hello')"

    def test_whitespace_trimmed(self):
        assert _strip_code_fences("  code  ") == "code"


class TestGlbValidation:
    def test_parse_valid_glb_json(self):
        glb = _make_glb({"nodes": [{"name": "Hips"}]})
        parsed = _parse_glb_json(glb)
        assert parsed is not None
        assert parsed["nodes"][0]["name"] == "Hips"

    def test_parse_invalid_magic(self):
        bad = b"XXXX" + b"\x00" * 20
        assert _parse_glb_json(bad) is None

    def test_parse_too_short(self):
        assert _parse_glb_json(b"\x00" * 5) is None

    def test_validate_glb_all_bones_present(self):
        glb = _make_valid_biped_glb()
        missing = _validate_glb(glb, bone_names("biped"))
        assert missing == []

    def test_validate_glb_missing_bones(self):
        # Only 3 bones — most are missing.
        glb = _make_glb({"nodes": [{"name": "Hips"}, {"name": "Head"}, {"name": "Spine"}]})
        missing = _validate_glb(glb, bone_names("biped"))
        assert "LeftArm" in missing
        assert "RightFoot" in missing
        assert len(missing) > 10

    def test_validate_glb_no_nodes(self):
        glb = _make_glb({"nodes": []})
        missing = _validate_glb(glb, bone_names("biped"))
        assert len(missing) == 25

    def test_validate_glb_unparseable(self):
        missing = _validate_glb(b"\x00" * 30, bone_names("biped"))
        assert len(missing) == 1
        assert "unparseable" in missing[0].lower()


class TestScaffoldMerge:
    def test_merge_replaces_marker(self):
        llm_code = "bpy.ops.mesh.primitive_cube_add()"
        merged = blender_llm_pipeline._merge_scaffold(llm_code)
        # The marker inside _build_body should be replaced; the docstring
        # mention is fine.  The LLM code must appear.
        assert "primitive_cube_add" in merged
        assert merged.count("__BUILD_BODY__") == 1  # only the docstring mention

    def test_merge_indents_code(self):
        llm_code = "if True:\n    pass"
        merged = blender_llm_pipeline._merge_scaffold(llm_code)
        # The code should be indented inside _build_body.  The scaffold keeps a
        # trailing "# noqa: F821 -- placeholder marker" comment on the pass
        # line — match by prefix, not equality.
        lines = merged.split("\n")
        body_lines = [l for l in lines if l.lstrip().startswith("pass")]
        assert body_lines
        # 8 spaces = 4 (function body indent from marker) + 4 (if-body indent in source).
        assert body_lines[0].startswith("        pass")


class TestExecuteBlenderScript:
    @pytest.mark.asyncio
    async def test_blender_not_found(self, monkeypatch):
        async def _raise(*a, **kw):
            raise FileNotFoundError

        monkeypatch.setattr(blender_llm_pipeline.asyncio, "create_subprocess_exec", _raise)
        result = await blender_llm_pipeline._execute_blender_script("pass", {"front": "", "right": "", "back": ""})
        assert result.success is False
        assert "not found" in result.stderr

    @pytest.mark.asyncio
    async def test_successful_execution(self, monkeypatch, tmp_path):
        """Mock the full subprocess + file I/O inside _execute_blender_script."""
        valid_glb = _make_valid_biped_glb()

        # Patch tempfile.TemporaryDirectory to use our tmp_path so we can
        # pre-create the expected output files.
        class _FakeTempDir:
            def __enter__(self):
                return tmp_path

            def __exit__(self, *a):
                pass

        monkeypatch.setattr(blender_llm_pipeline.tempfile, "TemporaryDirectory", _FakeTempDir)

        (tmp_path / "output.glb").write_bytes(valid_glb)
        (tmp_path / "preview.png").write_bytes(b"\x89PNG fake")

        fake_proc = AsyncMock()
        fake_proc.returncode = 0
        fake_proc.stdout = MagicMock()
        fake_proc.stdout.read = AsyncMock(return_value=b"[scaffold] done")
        fake_proc.stderr = MagicMock()
        fake_proc.stderr.read = AsyncMock(return_value=b"")

        async def _fake_wait_for(coro, timeout=None):
            return await coro

        monkeypatch.setattr(blender_llm_pipeline.asyncio, "create_subprocess_exec", AsyncMock(return_value=fake_proc))
        monkeypatch.setattr(blender_llm_pipeline.asyncio, "wait_for", _fake_wait_for)
        monkeypatch.setattr(SETTINGS, "blender_llm_timeout", 60)

        result = await blender_llm_pipeline._execute_blender_script("pass", {"front": "", "right": "", "back": ""})
        assert result.success is True
        assert result.glb_bytes == valid_glb
        assert result.preview_png is not None


class TestPipelineIntegration:
    @pytest.mark.asyncio
    async def test_pipeline_marks_failed_when_all_iterations_fail(self, monkeypatch):
        """All iterations produce errors → model row is marked failed."""
        # Mock the DB session for _emit_progress / _emit_model_failed.
        emit_calls: list[tuple[str, str, int]] = []

        def _fake_emit_progress(user_id, stage, pct, *, provider=None):
            emit_calls.append((stage, str(pct), str(user_id)))

        monkeypatch.setattr(blender_llm_pipeline, "_emit_progress", _fake_emit_progress)
        monkeypatch.setattr(blender_llm_pipeline, "_emit_model_failed", lambda uid, reason: emit_calls.append(("failed", reason, str(uid))))
        monkeypatch.setattr(blender_llm_pipeline, "_emit_model_ready", lambda *a, **kw: emit_calls.append(("ready", "", "")))

        # Mock select_rig_type to avoid LLM calls.
        monkeypatch.setattr(blender_llm_pipeline, "select_rig_type", AsyncMock(return_value="biped"))

        # Mock seed loading to avoid file I/O.
        monkeypatch.setattr(blender_llm_pipeline, "_resolve_seeds", _fake_resolve_seeds)

        monkeypatch.setattr(blender_llm_pipeline, "_llm_generate_script", AsyncMock(return_value="pass"))
        monkeypatch.setattr(blender_llm_pipeline, "_vision_llm_call", AsyncMock(return_value="pass"))

        monkeypatch.setattr(
            blender_llm_pipeline,
            "_execute_blender_script",
            AsyncMock(return_value=BlenderResult(success=False, stderr="blender error")),
        )

        monkeypatch.setattr(SETTINGS, "blender_llm_max_iterations", 2)

        # Mock the shared helpers to avoid real DB.
        fail_calls: list[tuple[int, str]] = []
        monkeypatch.setattr(blender_llm_pipeline, "_mark_generation_failed", lambda mid, reason: fail_calls.append((mid, reason)))

        monkeypatch.setattr(blender_llm_pipeline, "SESSION_LOCAL", _FakeSession)

        await blender_llm_pipeline.run_blender_llm_pipeline(1, {"front": "f.jpg", "right": "r.jpg", "back": "b.jpg"}, "人类", 1)

        # Verify model was marked failed.
        assert len(fail_calls) == 1
        assert fail_calls[0][0] == 1
        assert any(stage == "failed" for stage, *_ in emit_calls)

    @pytest.mark.asyncio
    async def test_pipeline_succeeds_on_first_iteration(self, monkeypatch):
        """Script executes successfully, validation passes, LLM says converged."""
        emit_calls: list[tuple[str, str, str]] = []

        def _fake_emit_progress(user_id, stage, pct, *, provider=None):
            emit_calls.append((stage, str(pct), ""))

        monkeypatch.setattr(blender_llm_pipeline, "_emit_progress", _fake_emit_progress)
        monkeypatch.setattr(blender_llm_pipeline, "_emit_model_ready", lambda *a, **kw: emit_calls.append(("ready", "", "")))
        monkeypatch.setattr(blender_llm_pipeline, "_emit_model_failed", lambda uid, reason: emit_calls.append(("failed", reason, "")))

        monkeypatch.setattr(blender_llm_pipeline, "select_rig_type", AsyncMock(return_value="biped"))
        monkeypatch.setattr(blender_llm_pipeline, "_resolve_seeds", _fake_resolve_seeds)

        monkeypatch.setattr(blender_llm_pipeline, "_llm_generate_script", AsyncMock(return_value="bpy.ops.mesh.primitive_cube_add()"))

        valid_glb = _make_valid_biped_glb()
        monkeypatch.setattr(
            blender_llm_pipeline,
            "_execute_blender_script",
            AsyncMock(return_value=BlenderResult(success=True, glb_bytes=valid_glb, preview_png=b"\x89PNG fake")),
        )

        monkeypatch.setattr(blender_llm_pipeline, "_inject_morph_targets", AsyncMock(return_value=valid_glb))
        monkeypatch.setattr(blender_llm_pipeline, "_extract_morph_names_from_glb", lambda data: ["eyeBlinkLeft"])

        monkeypatch.setattr(
            blender_llm_pipeline,
            "_llm_evaluate",
            AsyncMock(return_value=EvaluationResult(score=9, converged=True, critique="good")),
        )

        monkeypatch.setattr(blender_llm_pipeline, "save_companion_model", lambda data, user_id: f"companion-models/{user_id}/model_test.glb")

        # Mock the shared finalize helper — returns True (activated, not superseded).
        finalize_calls: list[dict] = []
        monkeypatch.setattr(
            blender_llm_pipeline, "_finalize_generation",
            lambda model_id, user_id, **kw: (finalize_calls.append(kw), True)[1],
        )
        monkeypatch.setattr(blender_llm_pipeline, "_mark_generation_failed", lambda mid, reason: None)

        monkeypatch.setattr(blender_llm_pipeline, "SESSION_LOCAL", _FakeSession)
        monkeypatch.setattr(SETTINGS, "blender_llm_max_iterations", 5)

        await blender_llm_pipeline.run_blender_llm_pipeline(1, {"front": "f.jpg", "right": "r.jpg", "back": "b.jpg"}, "人类", 42)

        # Verify finalize was called with the right provider.
        assert len(finalize_calls) == 1
        assert finalize_calls[0]["provider"] == "blender_llm"
        assert finalize_calls[0]["rig_type"] == "biped"
        assert any(stage == "ready" for stage, *_ in emit_calls)
