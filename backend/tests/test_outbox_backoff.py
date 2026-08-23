"""网关重试退避计算守护。"""

import random
from datetime import timedelta

import pytest
from services.gateway.connection import compute_backoff


@pytest.fixture(autouse=True)
def _seed_random():
    random.seed(0)
    yield
    random.seed()


def _seconds(td: timedelta) -> float:
    return td.total_seconds()


class TestComputeBackoffBoundaries:
    """每个 retry_count 级别:结果应在 ``[base/2, base]`` 闭区间内。"""

    @pytest.mark.parametrize(
        "retry_count,expected_base",
        [
            (0, 1),  # base=1, half=0.5
            (1, 2),
            (3, 8),
            (6, 60),  # 2^6=64 → cap 60
        ],
    )
    def test_within_jitter_band(self, retry_count: int, expected_base: int):
        for _ in range(200):
            td = compute_backoff(retry_count)
            base_half = expected_base / 2
            assert base_half <= _seconds(td) <= expected_base, f"retry_count={retry_count} produced {_seconds(td)}s outside [{base_half}, {expected_base}]"

    def test_cap_above_pow2_ceiling(self):
        # retry_count=10 时 base=min(60, 1024)=60,half=30 → 区间 [30, 60]
        for _ in range(200):
            td = compute_backoff(10)
            assert 30.0 <= _seconds(td) <= 60.0


class TestComputeBackoffDistribution:
    """Equal Jitter 均值与随机性校验。"""

    def test_mean_value_at_retry_count_3(self):
        samples = [compute_backoff(3).total_seconds() for _ in range(2000)]
        mean = sum(samples) / len(samples)
        assert 5.5 <= mean <= 6.5, f"expected mean ≈ 6.0, got {mean:.4f}"

    def test_not_deterministic(self):
        values = {compute_backoff(3).total_seconds() for _ in range(20)}
        assert len(values) >= 15
