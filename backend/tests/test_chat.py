import io
import json
import struct
import zlib
from types import SimpleNamespace

import pytest


class TestResolveContextTokens:
    # 三层：env 覆盖 → provider 默认 → 终态兜底。

    def test_global_fallback_on_unsupported_capability(self, caplog):
        # provider 未发布该 cap 的默认 → 走终态兜底，同时输出 warning 让静默遗漏可见。
        import logging

        from services.llm import resolve_context_tokens

        with caplog.at_level(logging.WARNING, logger="services.llm.providers"):
            assert resolve_context_tokens("mimo", "video_gen") == 1_000_000
        assert any("no default published" in rec.message and rec.levelno == logging.WARNING for rec in caplog.records)

    def test_env_override_wins(self, monkeypatch):
        from components import SETTINGS
        from services.llm import resolve_context_tokens

        monkeypatch.setattr(SETTINGS, "llm_context_tokens", 500_000)
        assert resolve_context_tokens("mimo", "llm") == 500_000

    def test_zero_string_override_falls_through(self):
        # ``.env`` 是空/0/非数字时必须折叠为 None，避免触发 Pydantic 的 ``Field(gt=0)``。
        from components.config import Settings

        for raw in ("0", "-1", "", "abc"):
            assert Settings.model_validate({"llm_context_tokens": raw}).llm_context_tokens is None, raw

    def test_image_attachment_uses_image_url_part(self):
        from modules.system import ChatMessageRequest
        from services.chat.persistence import _build_persisted_content

        req = SimpleNamespace(
            message=ChatMessageRequest(
                role="user",
                content="Describe this image",
                attachments=[{"type": "image", "file_url": "http://example.com/image.png"}],
            )
        )
        content, content_type = _build_persisted_content(req)
        assert content_type == "multimodal_v1"
        parsed = json.loads(content)
        assert len(parsed) == 2
        image_part = parsed[1]
        assert image_part["type"] == "input_image"
        assert image_part["image_url"] == "http://example.com/image.png"

def _make_silent_wav(duration_sec: float = 1.0, sample_rate: int = 8000) -> bytes:
    num_samples = int(sample_rate * duration_sec)
    data = b"\x00\x00" * num_samples
    data_size = len(data)
    file_size = 36 + data_size

    buf = io.BytesIO()
    buf.write(b"RIFF")
    buf.write(file_size.to_bytes(4, "little"))
    buf.write(b"WAVE")
    buf.write(b"fmt ")
    buf.write((16).to_bytes(4, "little"))
    buf.write((1).to_bytes(2, "little"))
    buf.write((1).to_bytes(2, "little"))
    buf.write(sample_rate.to_bytes(4, "little"))
    buf.write((sample_rate * 2).to_bytes(4, "little"))
    buf.write((2).to_bytes(2, "little"))
    buf.write((16).to_bytes(2, "little"))
    buf.write(b"data")
    buf.write(data_size.to_bytes(4, "little"))
    buf.write(data)
    return buf.getvalue()


def _make_tiny_png() -> bytes:
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    ihdr_crc = zlib.crc32(b"IHDR" + ihdr_data)
    ihdr = struct.pack(">I", 13) + b"IHDR" + ihdr_data + struct.pack(">I", ihdr_crc & 0xFFFFFFFF)
    raw_data = b"\x00\xff\x00\x00"
    compressed = zlib.compress(raw_data)
    idat_crc = zlib.crc32(b"IDAT" + compressed)
    idat = struct.pack(">I", len(compressed)) + b"IDAT" + compressed + struct.pack(">I", idat_crc & 0xFFFFFFFF)
    iend_crc = zlib.crc32(b"IEND")
    iend = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", iend_crc & 0xFFFFFFFF)
    return sig + ihdr + idat + iend


@pytest.mark.e2e
class TestChatE2E:
    async def test_stateless_completion(self, test_client, test_token):
        """非 mock，跑真实的 /api/llm/completion 端点。"""
        headers = {"Authorization": f"Bearer {test_token}"}
        payload = {
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "Say 'hello' in one word."}]}],
            "model": "mimo-v2.5-pro",
            "temperature": 0.5,
            "max_output_tokens": 50,
        }
        resp = await test_client.post("/api/llm/completion", headers=headers, json=payload)
        assert resp.status_code == 200
        body = resp.json()
        assert body["content"] is not None
        assert len(body["content"]) > 0

    async def test_websocket_chat_flow(self, test_app, test_token, ws_ticket, monkeypatch, _patch_db):
        """非 mock，跑真实 WebSocket 聊天流：从建会话到 prompt 完成。"""
        # httpx 没有 WebSocket 支持——同步 TestClient 通过 portal loop 驱动 async app（共享 aiosqlite 连接）。
        from fastapi.testclient import TestClient
        from services.gateway import handlers

        # ``services.gateway.handlers`` 不在 conftest 的 SESSION_LOCAL patch 列表（直接 import 绑定）——按其他模块的模式把 WS 启动 session 指向测试 DB。
        monkeypatch.setattr(handlers, "SESSION_LOCAL", _patch_db[1])

        test_client = TestClient(test_app)
        with test_client.websocket_connect(f"/api/chat/ws?ticket={ws_ticket}") as ws:
            ws.send_json({"jsonrpc": "2.0", "id": 1, "method": "session.create", "params": {}})
            resp = ws.receive_json()
            assert "result" in resp, f"Unexpected response: {resp}"
            session_id = resp["result"]["session_id"]
            assert session_id

            ws.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "prompt.submit",
                    "params": {
                        "session_id": session_id,
                        "text": "Say 'ok' in one word.",
                    },
                }
            )
            resp = ws.receive_json()
            assert resp["result"] == {"queued": True}

            events = []
            while True:
                msg = ws.receive_json()
                if msg.get("method") == "event":
                    params = msg.get("params", {})
                    event_type = params.get("type")
                    if event_type == "error":
                        raise RuntimeError(f"Chat turn failed with error: {params.get('payload') or params.get('message')}")
                    events.append(params)
                    if event_type == "message.complete":
                        break

            types = [e["type"] for e in events]
            assert "message.start" in types
            assert "message.complete" in types

            deltas = [e for e in events if e["type"] == "message.delta"]
            full_text = "".join(e.get("payload", {}).get("text", "") for e in deltas)
            assert len(full_text) > 0

    async def test_websocket_auth_rejection(self, test_app):
        """无效 token 时 WebSocket 应当被拒绝。"""
        from fastapi.testclient import TestClient
        from starlette.testclient import WebSocketDisconnect

        test_client = TestClient(test_app)
        with pytest.raises(WebSocketDisconnect):
            with test_client.websocket_connect("/api/chat/ws?ticket=invalid-ticket-abc"):
                pass

    async def test_websocket_session_lifecycle(self, test_app, test_token, ws_ticket, monkeypatch, _patch_db):
        """建会话并在 interrupt 后验证 prompt 提交仍可用。"""
        from fastapi.testclient import TestClient
        from services.gateway import handlers

        # 同上一个聊天流测试中，conftest patch-list 的遗漏。
        monkeypatch.setattr(handlers, "SESSION_LOCAL", _patch_db[1])

        test_client = TestClient(test_app)
        with test_client.websocket_connect(f"/api/chat/ws?ticket={ws_ticket}") as ws:
            ws.send_json({"jsonrpc": "2.0", "id": 1, "method": "session.create", "params": {}})
            resp = ws.receive_json()
            session_id = resp["result"]["session_id"]

            # interrupt（无 turn 时是 no-op）——验证刚挂载的会话能命中 handler。
            ws.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "session.interrupt",
                    "params": {"session_id": session_id},
                }
            )
            resp = ws.receive_json()
            assert resp["result"] == {}


@pytest.mark.asyncio
async def test_chat_tool_batch_cancellation_persists_cancelled_results_and_summary(_patch_db, monkeypatch):
    import asyncio
    import json

    from modules.conversation import Conversation, Message
    from services.chat import persistence
    from services.chat.persistence import (
        _persist_assistant_with_tool_calls_and_results,
        _ToolDispatchContext,
        persist_tool_summary,
    )
    from services.llm import copy_responses_context
    from sqlalchemy import select

    _, SessionLocal = _patch_db

    async with SessionLocal() as db:
        conv = Conversation(user_id=1, kind="main", title="Main Conversation")
        db.add(conv)
        await db.commit()
        await db.refresh(conv)
        conv_id = conv.id

    async def _cancelled_tool_batch(*a, **kw):
        raise asyncio.CancelledError()

    monkeypatch.setattr(persistence, "_run_tool_batch", _cancelled_tool_batch)

    tool_calls = [{"type": "function_call", "call_id": "call_123", "name": "test_tool", "arguments": "{}"}]
    context = {"instructions": "SYS", "input": []}
    active_tools = {"test_tool"}
    schemas = {}
    dispatch_ctx = _ToolDispatchContext(user_id=1, llm_config={}, user_settings={}, session_id="s1", native_memory=None, guardrails=None, emitter=None)

    with pytest.raises(asyncio.CancelledError):
        await _persist_assistant_with_tool_calls_and_results(
            conv,
            tool_calls,
            "assistant thoughts",
            10,
            20,
            100,
            dispatch_ctx,
            context,
            active_tools,
            schemas,
        )

    await persist_tool_summary(conv, {"test_tool"})

    async with SessionLocal() as db:
        messages = (await db.execute(select(Message).where(Message.conversation_id == conv_id).order_by(Message.id))).scalars().all()
        assert len(messages) == 3

        assistant_msg, tool_msg, summary_msg = messages
        assert assistant_msg.role == "assistant"
        assert "call_123" in assistant_msg.tool_calls

        assert tool_msg.role == "tool"
        assert tool_msg.tool_call_id == "call_123"
        assert json.loads(tool_msg.content) == {"error": "cancelled"}

        assert summary_msg.role == "system"
        assert summary_msg.subtype == "tool_summary"
        assert "test_tool" in summary_msg.content
