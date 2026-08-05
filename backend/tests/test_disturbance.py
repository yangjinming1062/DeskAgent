import pytest

from services import disturbance


@pytest.fixture(autouse=True)
def _reset_state():
    disturbance._disturbance.clear()
    disturbance._user_preferred_tiers.clear()
    disturbance._focus_contexts.clear()
    yield
    disturbance._disturbance.clear()
    disturbance._user_preferred_tiers.clear()
    disturbance._focus_contexts.clear()


def test_compute_effective_tier_default_normal():
    assert disturbance.compute_effective_tier(42) == "normal"


def test_compute_effective_tier_respects_user_preferred():
    disturbance.set_user_preferred_tier(42, "proactive")
    assert disturbance.compute_effective_tier(42) == "proactive"


def test_manual_quiet_is_locked_in_against_focus_context():
    disturbance.set_user_preferred_tier(42, "quiet")
    disturbance.record_focus_context(42, immersive=True, fullscreen=True)
    assert disturbance.compute_effective_tier(42) == "quiet"

    disturbance.record_focus_context(42, immersive=False, fullscreen=False)
    assert disturbance.compute_effective_tier(42) == "quiet"


def test_immersive_overrides_proactive_to_quiet():
    disturbance.set_user_preferred_tier(42, "proactive")
    disturbance.record_focus_context(42, immersive=True, fullscreen=False)
    assert disturbance.compute_effective_tier(42) == "quiet"


def test_fullscreen_overrides_normal_to_quiet():
    disturbance.set_user_preferred_tier(42, "normal")
    disturbance.record_focus_context(42, immersive=False, fullscreen=True)
    assert disturbance.compute_effective_tier(42) == "quiet"


def test_clearing_focus_restores_user_preferred():
    disturbance.set_user_preferred_tier(42, "normal")
    disturbance.record_focus_context(42, immersive=True, fullscreen=True)
    assert disturbance.compute_effective_tier(42) == "quiet"

    disturbance.record_focus_context(42, immersive=False, fullscreen=False)
    assert disturbance.compute_effective_tier(42) == "normal"


def test_set_disturbance_tier_normalises_invalid():
    assert disturbance.set_disturbance_tier(1, "loud") == "normal"
    assert disturbance.get_disturbance_tier(1) == "normal"


def test_set_user_preferred_tier_normalises_invalid():
    assert disturbance.set_user_preferred_tier(1, "huge") == "normal"
    assert disturbance.get_user_preferred_tier(1) == "normal"


def test_is_quiet_reflects_effective_tier():
    disturbance.set_user_preferred_tier(1, "proactive")
    disturbance.set_disturbance_tier(1, "quiet")
    assert disturbance.is_quiet(1) is True