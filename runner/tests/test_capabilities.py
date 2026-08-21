from unittest.mock import patch

from utils.capabilities import (
    local_stt_available,
    local_tts_available,
    microphone_available,
    probe_local_stt,
    probe_local_tts,
    probe_microphone,
    probe_screen_capture,
    probe_system_activity,
    reset_snapshot_cache,
    screen_capture_available,
    snapshot,
    snapshot_health,
    snapshot_with_health,
    system_activity_available,
)


def test_individual_probes_return_tuples():
    """All probe_* functions must return (bool, str | None) tuples."""
    mic_ok, mic_reason = probe_microphone()
    assert isinstance(mic_ok, bool)
    assert mic_reason is None or isinstance(mic_reason, str)
    assert microphone_available() == mic_ok

    screen_ok, screen_reason = probe_screen_capture()
    assert isinstance(screen_ok, bool)
    assert screen_reason is None or isinstance(screen_reason, str)
    assert screen_capture_available() == screen_ok

    stt_ok, stt_reason = probe_local_stt()
    assert isinstance(stt_ok, bool)
    assert stt_reason is None or isinstance(stt_reason, str)
    assert local_stt_available() == stt_ok

    tts_ok, tts_reason = probe_local_tts()
    assert isinstance(tts_ok, bool)
    assert tts_reason is None or isinstance(tts_reason, str)
    assert local_tts_available() == tts_ok

    sys_ok, sys_reason = probe_system_activity()
    assert isinstance(sys_ok, bool)
    assert sys_reason is None or isinstance(sys_reason, str)
    assert system_activity_available() == sys_ok


def test_snapshot_returns_boolean_map():
    """snapshot() must preserve backwards-compatible boolean dict."""
    reset_snapshot_cache()
    caps = snapshot()
    assert isinstance(caps, dict)
    expected_keys = {"microphone", "screen_capture", "local_stt", "local_tts", "system_activity", "platform", "python"}
    assert expected_keys.issubset(caps.keys())
    assert isinstance(caps["microphone"], bool)
    assert isinstance(caps["screen_capture"], bool)
    assert isinstance(caps["platform"], str)


def test_snapshot_with_health_structure():
    """snapshot_with_health() must return (caps, health) matching detailed health contract."""
    reset_snapshot_cache()
    caps, health = snapshot_with_health()
    assert isinstance(caps, dict)
    assert isinstance(health, dict)

    for key in ("microphone", "screen_capture", "local_stt", "local_tts", "system_activity"):
        assert key in health
        assert "available" in health[key]
        assert "reason" in health[key]
        assert health[key]["available"] == caps[key]
        if health[key]["available"]:
            assert health[key]["reason"] is None
        else:
            assert isinstance(health[key]["reason"], str)


def test_snapshot_ttl_caching():
    """snapshot_with_health should cache results for 30s."""
    reset_snapshot_cache()
    with patch("utils.capabilities.microphone_available", return_value=True) as mock_mic:
        caps1, health1 = snapshot_with_health()
        assert mock_mic.call_count == 1
        assert caps1["microphone"] is True
        assert health1["microphone"]["available"] is True

        # Second call within TTL should return cached object
        caps2, health2 = snapshot_with_health()
        assert mock_mic.call_count == 1
        assert caps2 == caps1

        # Also snapshot_health() uses the cache
        health3 = snapshot_health()
        assert mock_mic.call_count == 1
        assert health3 == health1
