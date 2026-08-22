import pytest
from services.companion import classify_species, select_rig_type


class _FakeChat:
    def __init__(self, content: str | Exception = '{"rig_type": "biped", "has_humanoid_face": true}'):
        self.content = content
        self.calls: list[dict] = []

    async def __call__(
        self,
        db,
        user_id,
        system_prompt,
        user_payload,
        *,
        provider_config=None,
    ):
        self.calls.append(
            {
                "db": db,
                "user_id": user_id,
                "system": system_prompt,
                "user": user_payload,
            },
        )
        if isinstance(self.content, Exception):
            raise self.content
        return self.content


@pytest.mark.asyncio
async def test_classify_species_parses_json_verdict():
    chat = _FakeChat(content='{"rig_type": "quadruped", "has_humanoid_face": false}')
    assert await classify_species(chat, "机械狼") == ("quadruped", False)
    assert len(chat.calls) == 1
    assert "机械狼" in chat.calls[0]["user"]
    assert "has_humanoid_face" in chat.calls[0]["system"]


@pytest.mark.asyncio
async def test_classify_species_strips_markdown_fence_and_prose():
    chat = _FakeChat(content='好的，分类如下：\n```json\n{"rig_type": "avian", "has_humanoid_face": true}\n```')
    assert await classify_species(chat, "天使") == ("avian", True)


@pytest.mark.asyncio
async def test_classify_species_invalid_rig_falls_back_to_biped_keeps_face():
    chat = _FakeChat(content='{"rig_type": "invalid-type", "has_humanoid_face": false}')
    assert await classify_species(chat, "龙") == ("biped", False)


@pytest.mark.asyncio
async def test_classify_species_non_bool_face_defaults_true():
    chat = _FakeChat(content='{"rig_type": "serpentine", "has_humanoid_face": "yes"}')
    assert await classify_species(chat, "龙") == ("serpentine", True)


@pytest.mark.asyncio
async def test_classify_species_falls_back_to_default_on_chat_error():
    chat = _FakeChat(content=RuntimeError("network down"))
    assert await classify_species(chat, "狗") == ("biped", True)


@pytest.mark.asyncio
async def test_classify_species_falls_back_on_empty_or_non_json_response():
    for content in ("", "quadruped", "无关文本"):
        assert await classify_species(_FakeChat(content=content), "鱼") == ("biped", True)


@pytest.mark.asyncio
async def test_classify_species_uses_default_species_when_blank():
    chat = _FakeChat(content='{"rig_type": "biped", "has_humanoid_face": true}')
    await classify_species(chat, "   ")
    assert "人类" in chat.calls[0]["user"]


@pytest.mark.asyncio
async def test_classify_species_normalizes_case_and_whitespace():
    chat = _FakeChat(content='{"rig_type": "  BIPED  ", "has_humanoid_face": true}')
    assert await classify_species(chat, "人类") == ("biped", True)


@pytest.mark.asyncio
async def test_select_rig_type_returns_rig_half_only():
    chat = _FakeChat(content='{"rig_type": "hexapod", "has_humanoid_face": false}')
    assert await select_rig_type(chat, "甲虫") == "hexapod"
