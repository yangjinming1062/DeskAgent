import json

import pytest

from services.companion import analyze_personality_tags


@pytest.mark.asyncio
async def test_analyze_personality_tags_json_output():
    async def mock_chat(
        db, user_id, system_prompt, user_payload, *, provider_config=None
    ):
        return json.dumps(["活泼", "元气", "护主"])

    definition = json.dumps(
        {"name": "小白", "personality": "活泼爱动", "biological_type": "狗"}
    )
    tags = await analyze_personality_tags(
        mock_chat, definition, species="犬类", rig_type="quadruped"
    )

    assert tags == ["活泼", "元气", "护主"]


@pytest.mark.asyncio
async def test_analyze_personality_tags_allows_novel_tags():
    async def mock_chat(
        db, user_id, system_prompt, user_payload, *, provider_config=None
    ):
        return '["赛博朋克", "机械心", "冷血杀手"]'

    definition = json.dumps({"name": "T800", "personality": "冷酷机械"})
    tags = await analyze_personality_tags(mock_chat, definition, rig_type="biped")

    assert "赛博朋克" in tags
    assert "机械心" in tags
    assert "冷血杀手" in tags


@pytest.mark.asyncio
async def test_analyze_personality_tags_fallback_on_error():
    async def mock_failing_chat(
        db, user_id, system_prompt, user_payload, *, provider_config=None
    ):
        raise RuntimeError("LLM network timeout")

    definition = json.dumps({"name": "小光", "personality": "温柔"})
    tags = await analyze_personality_tags(mock_failing_chat, definition)

    assert isinstance(tags, list)
    assert len(tags) > 0
    assert "温柔" in tags
