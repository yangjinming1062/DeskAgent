import pytest
from components import backoff_for_poll


class TestBackoffExponentialGrowth:
    @pytest.mark.parametrize(
        "attempt, base, cap, expected",
        [
            (0, 5.0, 40.0, 5.0),
            (1, 5.0, 40.0, 10.0),
            (2, 5.0, 40.0, 20.0),
            (3, 5.0, 40.0, 40.0),
            (10, 5.0, 40.0, 40.0),
        ],
    )
    def test_deterministic_curve(self, attempt, base, cap, expected):
        assert backoff_for_poll(attempt, base_interval=base, max_interval=cap, jitter=False) == expected


class TestBackoffJitter:
    def test_within_band_at_attempt_3(self):
        base, cap = 5.0, 40.0
        for _ in range(200):
            v = backoff_for_poll(3, base_interval=base, max_interval=cap, jitter=True)
            assert 20.0 <= v <= 40.0

    def test_within_band_at_attempt_0(self):
        base, cap = 5.0, 40.0
        for _ in range(200):
            v = backoff_for_poll(0, base_interval=base, max_interval=cap, jitter=True)
            assert 2.5 <= v <= 5.0

    def test_jitter_distribution_mean_near_band_midpoint(self):
        import random

        rng_state = random.getstate()
        try:
            random.seed(123)
            samples = [backoff_for_poll(0, base_interval=5.0, max_interval=40.0, jitter=True) for _ in range(2000)]
        finally:
            random.setstate(rng_state)
        mean = sum(samples) / len(samples)
        assert 3.4 <= mean <= 4.1, f"expected mean ≈ 3.75, got {mean:.3f}"


class TestBackoffClamping:
    def test_negative_remaining_returns_zero(self):
        v = backoff_for_poll(2, base_interval=5.0, max_interval=40.0, remaining_seconds=-0.5, jitter=False)
        assert v == 0.0

    def test_zero_remaining_returns_zero(self):
        v = backoff_for_poll(2, base_interval=5.0, max_interval=40.0, remaining_seconds=0.0, jitter=False)
        assert v == 0.0

    def test_small_remaining_caps_sleep(self):
        v = backoff_for_poll(10, base_interval=5.0, max_interval=60.0, remaining_seconds=1.5, jitter=False)
        assert v == 1.5

    def test_large_remaining_no_clamp(self):
        v = backoff_for_poll(2, base_interval=5.0, max_interval=40.0, remaining_seconds=900.0, jitter=False)
        assert v == 20.0


class TestBackoffAttemptNormalisation:
    def test_negative_attempt_normalised_to_zero(self):
        v = backoff_for_poll(-3, base_interval=5.0, max_interval=40.0, jitter=False)
        assert v == 5.0

    def test_remaining_none_disables_clamp(self):
        v = backoff_for_poll(2, base_interval=5.0, max_interval=40.0, remaining_seconds=None, jitter=False)
        assert v == 20.0
