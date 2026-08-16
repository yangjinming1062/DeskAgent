import pytest

from services.companion.should_act import ALLOWED_ACTIONS, ShouldActResult, should_act


def test_should_act_result_model():
    res = ShouldActResult(should_act=True, action="roam", reason="test")
    assert res.should_act is True
    assert res.action == "roam"
    assert res.reason == "test"


def test_allowed_actions_set():
    assert "go_sleep" in ALLOWED_ACTIONS
    assert "wake" in ALLOWED_ACTIONS
    assert "roam" in ALLOWED_ACTIONS
    assert "perch" in ALLOWED_ACTIONS
    assert "stay" in ALLOWED_ACTIONS
    assert "dance" not in ALLOWED_ACTIONS


@pytest.mark.asyncio
async def test_should_act_invalid_kind():
    res = await should_act(user_id=1, kind="invalid_kind")
    assert res.should_act is False
    assert res.reason == "invalid_kind"
