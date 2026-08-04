import base64
import json
import unittest.mock as mock


def _one_sample_wav_b64() -> str:
    """Minimal 16-bit mono PCM RIFF/WAVE — only the ``size > 0`` check at the test boundary uses it."""
    import struct

    sample_rate = 16000
    duration_s = 0.05
    n_samples = int(sample_rate * duration_s)
    pcm = struct.pack(f"<{n_samples}h", *([0] * n_samples))
    data = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVE" + b"fmt " + struct.pack(
        "<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16
    ) + b"data" + struct.pack("<I", len(pcm)) + pcm

    return base64.b64encode(data).decode("ascii")


def _decode_mock(result: dict):
    def fake_decode(*args, **kwargs):
        return result

    return fake_decode


def _stub_wav_to_wav_pcm16(src, dst, max_bytes):
    # Decode is mocked — just enough bytes to pass the ``size > 0`` check.
    dst.write_bytes(b"FAKE_PCM")
    return dst


async def _invoke(args: dict, decode_result: dict):
    from tools.multimodal.audio import stt_tool

    with mock.patch.object(stt_tool, "_decode_and_transcribe", _decode_mock(decode_result)):
        with mock.patch.object(stt_tool, "wav_to_wav_pcm16", _stub_wav_to_wav_pcm16):
            return await stt_tool.speech_to_text_tool(args)


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def test_auto_detect_empty_result_returns_error():
    """Auto-detect mode with empty text → tool_error so IPC falls back to cloud."""
    payload = json.loads(_run(
        _invoke(
            {"audio_base64": _one_sample_wav_b64(), "language": "auto"},
            {"text": "", "language": "zh", "language_probability": 0.95, "segments": []},
        )
    ))

    assert payload["success"] is False
    assert "no segments" in payload["error"].lower()


def test_auto_detect_low_confidence_returns_error():
    """Auto-detect with whisper confidence < 0.5 → tool_error."""
    payload = json.loads(_run(
        _invoke(
            {"audio_base64": _one_sample_wav_b64(), "language": "auto"},
            {"text": "uhhh", "language": "en", "language_probability": 0.4, "segments": [{"text": "uhhh"}]},
        )
    ))

    assert payload["success"] is False
    assert "confidence" in payload["error"].lower()


def test_auto_detect_high_confidence_returns_success():
    """Auto-detect with confidence ≥ 0.5 and non-empty text → success."""
    payload = json.loads(_run(
        _invoke(
            {"audio_base64": _one_sample_wav_b64(), "language": "auto"},
            {"text": "您好", "language": "zh", "language_probability": 0.92, "segments": [{"text": "您好"}]},
        )
    ))

    assert payload["success"] is True
    assert payload["text"] == "您好"
    assert payload["language"] == "zh"


def test_explicit_language_skips_confidence_check():
    """Explicit ``language='zh'`` honors the caller's choice on low confidence."""
    payload = json.loads(_run(
        _invoke(
            {"audio_base64": _one_sample_wav_b64(), "language": "zh"},
            {"text": "hello", "language": "zh", "language_probability": 0.3, "segments": [{"text": "hello"}]},
        )
    ))

    assert payload["success"] is True
    assert payload["text"] == "hello"


def test_explicit_language_empty_result_returns_error():
    """Explicit language with empty text → tool_error so desktop silent_fallback promotes to cloud (P1-9)."""
    payload = json.loads(_run(
        _invoke(
            {"audio_base64": _one_sample_wav_b64(), "language": "zh"},
            {"text": "", "language": "zh", "language_probability": 1.0, "segments": []},
        )
    ))

    assert payload["success"] is False
    assert "no segments" in payload["error"].lower()


def test_no_language_arg_defaults_to_auto_detect():
    """Omitting ``language`` is equivalent to ``"auto"`` — whisper auto-detects."""
    payload = json.loads(_run(
        _invoke(
            {"audio_base64": _one_sample_wav_b64()},  # no language
            {"text": "", "language": "zh", "language_probability": 0.95, "segments": []},
        )
    ))

    # Empty text in auto-detect mode → tool_error (the same as language="auto").
    assert payload["success"] is False
