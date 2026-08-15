import sys

import pytest

from tools.skills.skills_tool import skill_matches_platform


@pytest.fixture
def fake_platform(monkeypatch: pytest.MonkeyPatch):
    def _set(value: str) -> None:
        monkeypatch.setattr(sys, "platform", value)

    return _set


def test_skill_matches_platform_mac_skill_on_macos(fake_platform) -> None:
    fake_platform("darwin")
    assert skill_matches_platform({"platforms": ["macos"]})


def test_skill_matches_platform_mac_skill_on_linux(fake_platform) -> None:
    fake_platform("linux")
    assert not skill_matches_platform({"platforms": ["macos"]})


def test_skill_matches_platform_windows_skill_on_win32(fake_platform) -> None:
    fake_platform("win32")
    assert skill_matches_platform({"platforms": ["windows"]})


def test_skill_matches_platform_windows_skill_on_linux(fake_platform) -> None:
    fake_platform("linux")
    assert not skill_matches_platform({"platforms": ["windows"]})


def test_skill_matches_platform_cross_platform_default(fake_platform) -> None:
    fake_platform("linux")
    assert skill_matches_platform({})
    assert skill_matches_platform({"platforms": ["linux", "macos", "windows"]})


def test_skill_matches_platform_accepts_legacy_singular(fake_platform) -> None:
    fake_platform("darwin")
    assert skill_matches_platform({"platform": "macos"})


def test_skill_matches_platform_handles_string_value(fake_platform) -> None:
    fake_platform("linux")
    assert skill_matches_platform({"platforms": "linux"})


def test_skill_matches_platform_unknown_string_falls_back_to_literal(
    fake_platform,
) -> None:
    # Unknown platforms fail closed — better to skip a skill than to run it
    # on a host we don't recognize.
    fake_platform("darwin")
    assert not skill_matches_platform({"platforms": ["plan9"]})


def test_skill_matches_platform_case_insensitive(fake_platform) -> None:
    fake_platform("darwin")
    assert skill_matches_platform({"platforms": ["MACOS"]})
    assert skill_matches_platform({"platforms": ["macOS"]})
