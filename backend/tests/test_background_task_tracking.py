import asyncio

import pytest
from services.companion import expression_avatar_service


@pytest.mark.asyncio
async def test_expression_warm_start_task_is_retained(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_resolve(**_kwargs):
        started.set()
        await release.wait()

    monkeypatch.setattr(expression_avatar_service, "resolve_expression_avatar", fake_resolve)
    expression_avatar_service.kick_background_generation(7, "happy")
    await asyncio.wait_for(started.wait(), timeout=1)

    assert len(expression_avatar_service._BACKGROUND_TASKS) == 1
    task = next(iter(expression_avatar_service._BACKGROUND_TASKS))
    release.set()
    await asyncio.wait_for(task, timeout=1)
    assert not expression_avatar_service._BACKGROUND_TASKS
