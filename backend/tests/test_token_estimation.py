from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from components.functions import approx_text_tokens
from modules.conversation import Message
from services.chat.turn_inputs import _find_authoritative_token_baseline, db_message_to_response_items
from services.llm.context_compressor import compress_history_if_needed
from services.llm.responses import approx_responses_tokens


def test_approx_text_tokens_cjk_and_punctuation():
    # 纯中文：4 个汉字 -> 4 * 1.3 = 5.2 -> 5 tokens
    zh = "你好世界"
    assert approx_text_tokens(zh) == 5

    # 中文标点、全角符号、破折号与引号 + 英文单词
    # 12 个全角/CJK/通用标点字符 (12 * 1.3 = 15.6) + 11 个 ASCII 字符 ((11 + 3) // 4 = 3) -> 18 tokens
    zh_punct = "“你好，世界！”——《SpiritAgent》"
    assert approx_text_tokens(zh_punct) == 18

    # 西文 ASCII：30 个字符 -> (30 + 3) // 4 = 8 tokens
    en = "Hello, world! This is a test."
    assert approx_text_tokens(en) == 8

    # 空文本
    assert approx_text_tokens("") == 0
    assert approx_text_tokens(None) == 0


def test_approx_responses_tokens_base64_image_protection():
    # 构造带有 50 万字符 Base64 Data URI 的图片消息
    huge_base64 = "data:image/png;base64," + "A" * 500_000
    items = [
        {"role": "user", "content": [{"type": "input_image", "image_url": huge_base64}]},
    ]
    # 绝不能遍历 image_url 导致几十万 token 假性暴增；固定 800 tokens + 角色外壳
    tokens = approx_responses_tokens("", items)
    assert 800 <= tokens <= 805


def test_db_message_to_response_items_multimodal_and_filtering():
    # 1. UI-only subtype 消息直接过滤 (如 hint)
    ui_msg = Message(role="assistant", content="...", subtype="hint")
    assert db_message_to_response_items(ui_msg) == []

    # 2. 多模态 JSON 正确解析
    multimodal_json = '[{"type": "input_text", "text": "看图"}, {"type": "input_image", "image_url": "http://img"}]'
    mm_msg = Message(role="user", content=multimodal_json, content_type="multimodal_v1")
    items = db_message_to_response_items(mm_msg)
    assert len(items) == 1
    assert items[0]["role"] == "user"
    assert len(items[0]["content"]) == 2

    # 3. 主会话 drop_tool_intermediates 行为
    tool_msg = Message(role="tool", content="output", tool_call_id="call_1")
    assert db_message_to_response_items(tool_msg, drop_tool_intermediates=True) == []
    assert len(db_message_to_response_items(tool_msg, drop_tool_intermediates=False)) == 1


def test_find_authoritative_token_baseline_scenarios():
    # 场景 1：会话首轮（仅 1 条 user 消息）-> 无基线
    history_turn1 = [Message(role="user", content="你好", prompt_tokens=0, completion_tokens=0)]
    baseline, subsequent = _find_authoritative_token_baseline(history_turn1, is_main_conversation=False)
    assert baseline is None
    assert subsequent == history_turn1

    # 场景 2：普通多轮对话（有前置 assistant 消息）-> 正确提取 prompt + completion tokens
    history_turn2 = [
        Message(role="user", content="你好", prompt_tokens=0, completion_tokens=0),
        Message(role="assistant", content="你好！", prompt_tokens=1200, completion_tokens=150),
        Message(role="user", content="帮我写代码", prompt_tokens=0, completion_tokens=0),
    ]
    baseline, subsequent = _find_authoritative_token_baseline(history_turn2, is_main_conversation=True)
    assert baseline == 1350
    assert len(subsequent) == 1
    assert subsequent[0].content == "帮我写代码"

    # 场景 3：主会话且前置 assistant 含有 tool_calls -> 为防基线虚高，必须废弃基线
    history_tool_main = [
        Message(role="user", content="查天气"),
        Message(role="assistant", content="", tool_calls='[{"name": "weather"}]', prompt_tokens=3000, completion_tokens=50),
        Message(role="tool", content="晴天", tool_call_id="call_1"),
        Message(role="user", content="明天呢？"),
    ]
    baseline_main, _ = _find_authoritative_token_baseline(history_tool_main, is_main_conversation=True)
    assert baseline_main is None

    # 场景 4：非主会话（保留完整工具帧）-> 允许使用含 tool_calls 的基线
    baseline_standard, _ = _find_authoritative_token_baseline(history_tool_main, is_main_conversation=False)
    assert baseline_standard == 3050

    # 场景 5：压缩检查点后首轮对话（历史从 compress_summary 开始）-> compress_summary 为 system 角色，不作为基线
    history_post_compress = [
        Message(role="system", content="[🗜️ 压缩摘要]", subtype="compress_summary", prompt_tokens=5000, completion_tokens=300),
        Message(role="user", content="继续"),
    ]
    baseline_post, subsequent_post = _find_authoritative_token_baseline(history_post_compress, is_main_conversation=True)
    assert baseline_post is None
    assert subsequent_post == history_post_compress


@pytest.mark.asyncio
async def test_compress_history_if_needed_triggers_with_current_tokens(monkeypatch):
    mock_response = SimpleNamespace(
        output_text="压缩后的内容摘要",
        status="completed",
        usage=SimpleNamespace(input_tokens=2500, output_tokens=180),
    )

    async def _mock_retry(_client, **_kwargs):
        return mock_response

    monkeypatch.setattr("services.llm.context_compressor.call_with_retry", _mock_retry)

    context = {
        "instructions": "系统提示词",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": f"消息 {i}"}]} for i in range(10)],
    }

    # 传入 current_tokens=3500，阈值 4000 * 0.8 = 3200 -> 触发压缩
    compressed, info = await compress_history_if_needed(
        context,
        client=AsyncMock(),
        model="demo-model",
        context_length=4000,
        enabled=True,
        threshold_ratio=0.8,
        current_tokens=3500,
    )

    assert info is not None
    assert info["summary"] == "压缩后的内容摘要"
    assert info["prompt_tokens"] == 2500
    assert info["completion_tokens"] == 180
    assert "[Conversation summary" in compressed["input"][0]["content"][0]["text"]
