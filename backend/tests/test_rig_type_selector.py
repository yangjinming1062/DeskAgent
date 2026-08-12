import pytest

from services.companion.rig_type_selector import select_rig_type


class _FakeChat:
    def __init__(self, content: str | Exception = "biped"):
        self.content = content
        self.calls: list[dict] = []

    async def __call__(self, db, user_id, system_prompt, user_payload, *, provider_config=None):
        self.calls.append({"db": db, "user_id": user_id, "system": system_prompt, "user": user_payload})
        if isinstance(self.content, Exception):
            raise self.content
        return self.content


@pytest.mark.asyncio
async def test_select_rig_type_returns_choice_for_known_species():
    chat = _FakeChat(content="quadruped")
    assert await select_rig_type(chat, "猫") == "quadruped"
    assert len(chat.calls) == 1
    assert "猫" in chat.calls[0]["user"]


@pytest.mark.asyncio
async def test_select_rig_type_falls_back_to_biped_on_invalid_response():
    chat = _FakeChat(content="invalid-type")
    assert await select_rig_type(chat, "龙") == "biped"


@pytest.mark.asyncio
async def test_select_rig_type_falls_back_to_biped_on_chat_error():
    chat = _FakeChat(content=RuntimeError("network down"))
    assert await select_rig_type(chat, "狗") == "biped"


@pytest.mark.asyncio
async def test_select_rig_type_normalizes_whitespace_and_case():
    chat = _FakeChat(content="  BIPED  ")
    assert await select_rig_type(chat, "人类") == "biped"


@pytest.mark.asyncio
async def test_select_rig_type_normalizes_trailing_punctuation():
    chat = _FakeChat(content="avian.")
    assert await select_rig_type(chat, "鹰") == "avian"


@pytest.mark.asyncio
async def test_select_rig_type_uses_default_species_when_blank():
    chat = _FakeChat(content="biped")
    await select_rig_type(chat, "   ")
    assert "人类" in chat.calls[0]["user"]


@pytest.mark.asyncio
async def test_select_rig_type_falls_back_on_empty_response():
    chat = _FakeChat(content="")
    assert await select_rig_type(chat, "鱼") == "biped"
