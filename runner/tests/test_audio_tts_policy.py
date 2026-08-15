import json
from pathlib import Path
from unittest import mock

import pytest

pytest.importorskip("tools.multimodal.audio.piper_runtime", reason="piper runtime not importable")


# ── text_language ─────────────────────────────────────────────────────────


def test_text_language_empty_and_whitespace_is_other():
    from tools.multimodal.audio import piper_runtime as pr

    assert pr.text_language("") == "other"
    assert pr.text_language("   \n\t  ") == "other"


def test_text_language_pure_chinese_is_zh():
    from tools.multimodal.audio import piper_runtime as pr

    assert pr.text_language("您好") == "zh"
    assert pr.text_language("您好，我叫小助手") == "zh"


def test_text_language_pure_english_is_other():
    from tools.multimodal.audio import piper_runtime as pr

    assert pr.text_language("Hello there") == "other"
    assert pr.text_language("This is a friendly greeting") == "other"


def test_text_language_mixed_short_english_heavy_is_other():
    """Short greets with one CJK token tilt to English under the 50% rule."""
    from tools.multimodal.audio import piper_runtime as pr

    # "Hi 你" — 1 CJK + 2 latin. cjk*2=2, len=3, 2<3 → other. (Mixed-greet
    # routing to the default en voice feels right; the operator's
    # preference kicks in via ``preferred`` when it actually matters.)
    assert pr.text_language("Hi 你") == "other"
    assert pr.text_language("hello world") == "other"


def test_text_language_mixed_long_chinese_heavy_is_zh():
    from tools.multimodal.audio import piper_runtime as pr

    # All CJK + Chinese punctuation. cjk*2 ≥ len → zh.
    text = "今天我们一起做的事情很多，请帮我看看这份文档"
    assert pr.text_language(text) == "zh"


# ── pick_voice_for_text ──────────────────────────────────────────────────


def test_pick_voice_for_text_explicit_preferred_wins():
    from tools.multimodal.audio import piper_runtime as pr

    # An explicit caller preference always wins.
    assert pr.pick_voice_for_text(preferred="en_US-amy-medium") == "en_US-amy-medium"


def test_pick_voice_for_text_chinese_text_picks_zh_default():
    from tools.multimodal.audio import piper_runtime as pr

    with mock.patch.object(pr, "default_voice_id", return_value="en_US-amy-medium"):
        assert pr.pick_voice_for_text() == pr.ZH_DEFAULT_VOICE


def test_pick_voice_for_text_english_text_picks_zh_default():
    """Non-CJK text still gets ZH_DEFAULT_VOICE — see runner/README §本地 TTS voice 选型 for the "auto-routing creates inconsistent identity" rationale."""
    from tools.multimodal.audio import piper_runtime as pr

    with mock.patch.object(pr, "default_voice_id", return_value="en_US-amy-medium"):
        assert pr.pick_voice_for_text() == pr.ZH_DEFAULT_VOICE


def test_pick_voice_for_text_empty_preferred_uses_zh_default():
    from tools.multimodal.audio import piper_runtime as pr

    # No caller pref → ZH default wins regardless of text language.
    assert pr.pick_voice_for_text() == pr.ZH_DEFAULT_VOICE


# ── _is_cloud_voice / cloud-id handling ───────────────────────────────────


def test_tts_tool_rejects_cloud_voice_ids():
    from tools.multimodal.audio import tts_tool

    # Calling text_to_speech_tool with a cloud voice id (e.g. ``冰糖``) must
    # return tool_error with a clear hint — silent local-fallback to Piper's
    # default would be the bug class this guard exists to prevent.
    result = tts_tool.text_to_speech_tool({"text": "hi", "voice": "冰糖"})
    payload = json.loads(result)
    assert payload["success"] is False
    assert "cloud-provider" in payload.get("error", "")


def test_tts_tool_rejects_mimo_voicedesign_token():
    from tools.multimodal.audio import tts_tool

    result = tts_tool.text_to_speech_tool(
        {"text": "hi", "voice": "mimo_voicedesign:cool girl"}
    )
    payload = json.loads(result)
    assert payload["success"] is False


def test_tts_tool_mimo_voicedesign_with_fallback_to_local(tmp_path: Path, monkeypatch):
    from tools.multimodal.audio import tts_tool

    _make_on_disk_voice(tmp_path, "zh_CN-huayan-medium")
    _patch_voice_dir(tmp_path, monkeypatch, tts_tool)

    def fake_piper(text, voice, speed, dst):
        dst.write_bytes(b"fake-wav")
        return {"engine": "piper", "voice": voice, "path": str(dst)}

    monkeypatch.setattr(tts_tool, "_synth_piper", fake_piper)

    result = tts_tool.text_to_speech_tool(
        {"text": "hi", "voice": "mimo_voicedesign:cool girl", "fallback_to_local": True}
    )
    payload = json.loads(result)
    assert payload["success"] is True
    assert payload["engine"] == "piper"
    assert payload["voice"] == "zh_CN-huayan-medium"


# ── voice id → repo path ──────────────────────────────────────────────────


def test_voice_id_to_repo_path_zh():
    from tools.multimodal.audio.piper_runtime import _voice_id_to_repo_path

    assert _voice_id_to_repo_path("zh_CN-huayan-medium") == "zh/zh_CN/huayan/medium"


def test_voice_id_to_repo_path_en():
    from tools.multimodal.audio.piper_runtime import _voice_id_to_repo_path

    assert _voice_id_to_repo_path("en_US-amy-medium") == "en/en_US/amy/medium"


def test_voice_id_to_repo_path_unknown_layout_returns_misc():
    from tools.multimodal.audio.piper_runtime import _voice_id_to_repo_path

    # No underscore in the lang region → fall through to ``misc/<id>``.
    assert _voice_id_to_repo_path("custom-id-without-lang") == "misc/custom-id-without-lang"


# ── bundled_voices ───────────────────────────────────────────────────────


def test_bundled_voices_includes_zh_default():
    from tools.multimodal.audio import piper_runtime as pr

    voices = pr.bundled_voices()
    assert pr.ZH_DEFAULT_VOICE in voices


def test_bundled_voices_includes_zh_male():
    from tools.multimodal.audio import piper_runtime as pr

    voices = pr.bundled_voices()
    assert pr.ZH_MALE_DEFAULT_VOICE in voices


def test_bundled_voices_includes_en_default():
    from tools.multimodal.audio import piper_runtime as pr

    voices = pr.bundled_voices()
    assert pr.EN_DEFAULT_VOICE in voices


def test_bundled_voices_voice_ids_match_piper_pattern():
    """Catches typos like ``zhiquan-medium`` (no region) at test time, not at onboarding."""
    from tools.multimodal.audio import piper_runtime as pr

    for vid in pr.bundled_voices():
        assert pr.PIPER_VOICE_RE.match(vid), f"bundled voice id {vid!r} does not match Piper pattern"


def test_discover_installed_voices(tmp_path: Path):
    from tools.multimodal.audio import piper_runtime as pr

    # Paired onnx + onnx.json
    (tmp_path / "zh_CN-custom-medium.onnx").write_text("model")
    (tmp_path / "zh_CN-custom-medium.onnx.json").write_text("{}")
    # Unpaired onnx (should not be discovered)
    (tmp_path / "zh_CN-incomplete-medium.onnx").write_text("model")

    discovered = pr.discover_installed_voices(tmp_path)
    assert discovered == ["zh_CN-custom-medium"]


# ── ensure_voice_installed ────────────────────────────────────────────────


def test_ensure_voice_installed_no_op_when_present(tmp_path: Path):
    from tools.multimodal.audio import piper_runtime as pr

    onnx = tmp_path / "zh_CN-huayan-medium.onnx"
    json_path = tmp_path / "zh_CN-huayan-medium.onnx.json"
    onnx.write_bytes(b"fake-onnx-bytes")
    json_path.write_text("{}", encoding="utf-8")

    with mock.patch.object(pr, "download_voice") as dl:
        assert pr.ensure_voice_installed("zh_CN-huayan-medium", voice_dir=tmp_path) is True
        dl.assert_not_called()


def test_ensure_voice_installed_download_failure_returns_false(tmp_path: Path):
    from tools.multimodal.audio import piper_runtime as pr

    with mock.patch.object(pr, "download_voice", side_effect=RuntimeError("network down")):
        # Returns False (no exception) — caller falls back to pyttsx3 or cloud.
        assert pr.ensure_voice_installed("zh_CN-huayan-medium", voice_dir=tmp_path) is False


def test_ensure_voice_installed_download_success(tmp_path: Path):
    from tools.multimodal.audio import piper_runtime as pr

    onnx = tmp_path / "zh_CN-huayan-medium.onnx"
    json_path = tmp_path / "zh_CN-huayan-medium.onnx.json"

    def fake_download(voice_id, voice_dir=None, timeout=60.0):
        onnx.write_bytes(b"fake-onnx")
        json_path.write_text("{}", encoding="utf-8")
        return onnx

    with mock.patch.object(pr, "download_voice", side_effect=fake_download):
        assert pr.ensure_voice_installed("zh_CN-huayan-medium", voice_dir=tmp_path) is True
        assert onnx.is_file() and json_path.is_file()


# ── Auto-mode engine fallback chain ──────────────────────────────────────


def _patch_voice_dir(tmp_path, monkeypatch, tts_tool):
    """Redirect both tts_tool.piper_voice_dir and piper_runtime's — the call site resolves via each function's own module namespace. Also stub ``piper_available`` and ``pyttsx3_available`` so the auto-mode chain doesn't bail on the optional dependency."""
    from tools.multimodal.audio import piper_runtime as pr

    monkeypatch.setattr(tts_tool, "piper_voice_dir", lambda: tmp_path)
    monkeypatch.setattr(pr, "piper_voice_dir", lambda: tmp_path)
    monkeypatch.setattr(tts_tool, "piper_available", lambda: True)
    monkeypatch.setattr(pr, "piper_available", lambda: True)
    monkeypatch.setattr(tts_tool, "pyttsx3_available", lambda: True)
    monkeypatch.setattr(pr, "pyttsx3_available", lambda: True)


def _make_on_disk_voice(tmp_path: Path, voice_id: str) -> None:
    """Helper: drop a fake onnx+json pair so `_piper_voice_ready` returns True."""
    (tmp_path / f"{voice_id}.onnx").write_bytes(b"fake-onnx")
    (tmp_path / f"{voice_id}.onnx.json").write_text("{}", encoding="utf-8")


def test_tts_auto_piper_succeeds_no_pyttsx3_call(tmp_path: Path, monkeypatch):
    """In auto mode, a working Piper voice is used and pyttsx3 never runs."""
    from tools.multimodal.audio import tts_tool

    _make_on_disk_voice(tmp_path, "zh_CN-huayan-medium")
    _patch_voice_dir(tmp_path, monkeypatch, tts_tool)

    piper_calls: list[str] = []
    pyttsx3_calls: list[str] = []

    def fake_piper(text, voice, speed, dst):
        piper_calls.append(voice)
        dst.write_bytes(b"fake-wav")
        return {"engine": "piper", "voice": voice, "path": str(dst)}

    def fake_pyttsx3(text, voice, speed, dst):
        pyttsx3_calls.append("called")
        dst.write_bytes(b"fake-wav")
        return {"engine": "pyttsx3", "voice": voice, "path": str(dst)}

    monkeypatch.setattr(tts_tool, "_synth_piper", fake_piper)
    monkeypatch.setattr(tts_tool, "_synth_pyttsx3", fake_pyttsx3)

    result = tts_tool.text_to_speech_tool({"text": "hi", "engine": "auto"})
    payload = json.loads(result)
    assert payload["success"] is True
    assert payload["engine"] == "piper"
    assert piper_calls == ["zh_CN-huayan-medium"]
    assert pyttsx3_calls == []  # Piper succeeded, pyttsx3 not consulted.


def test_tts_auto_piper_fails_silently_falls_back_to_pyttsx3(tmp_path: Path, monkeypatch):
    """Without silent Piper→pyttsx3 fallback, model corruption / OOM would surface as a hard TTS error."""
    from tools.multimodal.audio import tts_tool

    _make_on_disk_voice(tmp_path, "zh_CN-huayan-medium")
    _patch_voice_dir(tmp_path, monkeypatch, tts_tool)

    def fake_piper(text, voice, speed, dst):
        raise RuntimeError("piper model corruption")

    pyttsx3_calls: list[str] = []

    def fake_pyttsx3(text, voice, speed, dst):
        pyttsx3_calls.append("called")
        dst.write_bytes(b"fake-wav")
        return {"engine": "pyttsx3", "voice": voice, "path": str(dst)}

    monkeypatch.setattr(tts_tool, "_synth_piper", fake_piper)
    monkeypatch.setattr(tts_tool, "_synth_pyttsx3", fake_pyttsx3)

    result = tts_tool.text_to_speech_tool({"text": "hi", "engine": "auto"})
    payload = json.loads(result)
    assert payload["success"] is True, payload
    assert payload["engine"] == "pyttsx3"
    assert pyttsx3_calls == ["called"]


def test_tts_auto_piper_filenotfound_falls_back_to_pyttsx3(tmp_path: Path, monkeypatch):
    """FileNotFoundError during Piper synthesis → silent pyttsx3 fallback (most common "broken" state)."""
    from tools.multimodal.audio import tts_tool

    _make_on_disk_voice(tmp_path, "zh_CN-huayan-medium")
    _patch_voice_dir(tmp_path, monkeypatch, tts_tool)

    def fake_piper(text, voice, speed, dst):
        raise FileNotFoundError(f"{voice}.onnx missing")

    def fake_pyttsx3(text, voice, speed, dst):
        dst.write_bytes(b"fake-wav")
        return {"engine": "pyttsx3", "voice": voice, "path": str(dst)}

    monkeypatch.setattr(tts_tool, "_synth_piper", fake_piper)
    monkeypatch.setattr(tts_tool, "_synth_pyttsx3", fake_pyttsx3)

    result = tts_tool.text_to_speech_tool({"text": "hi", "engine": "auto"})
    payload = json.loads(result)
    assert payload["success"] is True
    assert payload["engine"] == "pyttsx3"


def test_tts_auto_both_engines_fail_returns_error(tmp_path: Path, monkeypatch):
    """Both Piper and pyttsx3 failing must surface a hard error hinting at `tts.engine=cloud`."""
    from tools.multimodal.audio import tts_tool

    _make_on_disk_voice(tmp_path, "zh_CN-huayan-medium")
    _patch_voice_dir(tmp_path, monkeypatch, tts_tool)

    def fake_piper(text, voice, speed, dst):
        raise RuntimeError("piper fail")

    def fake_pyttsx3(text, voice, speed, dst):
        raise RuntimeError("pyttsx3 fail")

    monkeypatch.setattr(tts_tool, "_synth_piper", fake_piper)
    monkeypatch.setattr(tts_tool, "_synth_pyttsx3", fake_pyttsx3)

    result = tts_tool.text_to_speech_tool({"text": "hi", "engine": "auto"})
    payload = json.loads(result)
    assert payload["success"] is False
    assert "all engines failed" in payload["error"]
    assert "tts.engine=cloud" in payload.get("hint", "")


def test_tts_explicit_piper_does_not_fall_back(tmp_path: Path, monkeypatch):
    """Explicit `engine=piper` must NOT silently swap to pyttsx3 on Piper failure."""
    from tools.multimodal.audio import tts_tool

    _make_on_disk_voice(tmp_path, "zh_CN-huayan-medium")
    _patch_voice_dir(tmp_path, monkeypatch, tts_tool)

    pyttsx3_called = False

    def fake_piper(text, voice, speed, dst):
        raise RuntimeError("piper fail")

    def fake_pyttsx3(text, voice, speed, dst):
        nonlocal pyttsx3_called
        pyttsx3_called = True
        dst.write_bytes(b"fake-wav")
        return {"engine": "pyttsx3", "voice": voice, "path": str(dst)}

    monkeypatch.setattr(tts_tool, "_synth_piper", fake_piper)
    monkeypatch.setattr(tts_tool, "_synth_pyttsx3", fake_pyttsx3)

    result = tts_tool.text_to_speech_tool({"text": "hi", "engine": "piper"})
    payload = json.loads(result)
    assert payload["success"] is False
    assert "piper fail" in payload["error"]
    assert not pyttsx3_called


def test_native_wav_to_pcm16_resampling(tmp_path: Path):
    import wave

    from tools.multimodal.audio import audio_io

    # Create a 44100Hz stereo WAV file using standard library
    src_wav = tmp_path / "stereo_44k.wav"
    dst_wav = tmp_path / "mono_16k.wav"

    with wave.open(str(src_wav), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(44100)
        # 44100 samples of stereo silence / simple pattern
        wf.writeframes(b"\x00\x00\x00\x00" * 44100)

    # Convert using audio_io
    out = audio_io.wav_to_wav_pcm16(src_wav, dst_wav)
    assert out.is_file()

    # Validate output format
    with wave.open(str(dst_wav), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getframerate() == 16000
        assert wf.getnframes() == 16000
