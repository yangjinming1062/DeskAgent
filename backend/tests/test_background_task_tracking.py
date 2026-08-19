import asyncio

import pytest
from modules.companion import WardrobeItem
from services.companion import expression_avatar_service, wardrobe_service


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


@pytest.mark.asyncio
async def test_wardrobe_texture_recovery_is_deduplicated(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_recovery(_user_id, _item):
        started.set()
        await release.wait()

    monkeypatch.setattr(wardrobe_service, "check_and_recover_missing_texture", fake_recovery)
    item = WardrobeItem(id=7, user_id=9, name="outfit", kind="texture", equipped=True)
    wardrobe_service._spawn_texture_recovery_once(9, item)
    wardrobe_service._spawn_texture_recovery_once(9, item)
    await asyncio.wait_for(started.wait(), timeout=1)

    assert set(wardrobe_service._TEXTURE_RECOVERY_TASKS) == {(9, 7)}
    task = wardrobe_service._TEXTURE_RECOVERY_TASKS[(9, 7)]
    release.set()
    await asyncio.wait_for(task, timeout=1)
    assert wardrobe_service._TEXTURE_RECOVERY_TASKS == {}
