import pytest
from services.companion.should_act import should_act


@pytest.mark.asyncio
async def test_should_act_invalid_kind():
    res = await should_act(user_id=1, kind="invalid_kind")
    assert res.should_act is False
    assert res.reason == "invalid_kind"
