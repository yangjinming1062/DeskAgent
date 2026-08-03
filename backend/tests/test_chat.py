import io
import json
import struct
import zlib
from types import SimpleNamespace

import pytest


# ── TestLLMClient (pure business logic, no mock) ────────────────────


class TestLLMClient:
    def test_client_for_config_uses_cached_factory(self):
        from services.llm import client_for_config

        cfg = {"api_key": "sk-a", "base_url": "https://a.example/v1"}
        a = client_for_config(cfg)
        b = client_for_config(cfg)
        assert a is b

    def test_client_for_config_distinguishes_by_key_pair(self):
        from services.llm import client_for_config

        a = client_for_config({"api_key": "sk-a", "base_url": "https://a.example/v1"})
        b = client_for_config({"api_key": "sk-b", "base_url": "https://a.example/v1"})
        c = client_for_config({"api_key": "sk-a", "base_url": "https://c.example/v1"})
        assert a is not b
        assert a is not c
        assert b is not c

    def test_client_for_config_keyerror_when_missing_keys(self):
        from services.llm import client_for_config

        with pytest.raises(KeyError):
            client_for_config({})
        with pytest.raises(KeyError):
            client_for_config({"api_key": "sk-only"})

    def test_mimo_models_in_hints(self):
        from components import MODEL_CONTEXT_TOKEN_HINTS

        assert "mimo-v2.5-pro" in MODEL_CONTEXT_TOKEN_HINTS
        assert "mimo-v2.5" in MODEL_CONTEXT_TOKEN_HINTS
        assert "mimo-v2.5-asr" in MODEL_CONTEXT_TOKEN_HINTS
        assert "mimo-v2.5-tts" in MODEL_CONTEXT_TOKEN_HINTS

    def test_no_openai_models_in_hints(self):
        from components import MODEL_CONTEXT_TOKEN_HINTS

        assert "gpt-4o" not in MODEL_CONTEXT_TOKEN_HINTS
        assert "claude-3-5-sonnet" not in MODEL_CONTEXT_TOKEN_HINTS
        assert "gemini-1.5-pro" not in MODEL_CONTEXT_TOKEN_HINTS

    def test_context_lengths(self):
        from components import MODEL_CONTEXT_TOKEN_HINTS

        assert MODEL_CONTEXT_TOKEN_HINTS["mimo-v2.5-pro"] == 1_000_000
        assert MODEL_CONTEXT_TOKEN_HINTS["mimo-v2.5-asr"] == 8_000
        assert MODEL_CONTEXT_TOKEN_HINTS["mimo-v2.5-tts"] == 8_000

    def test_mimo_detected(self):
        from services.chat.system_prompt import _is_mimo_family

        assert _is_mimo_family("mimo-v2.5-pro") is True
        assert _is_mimo_family("mimo-v2.5") is True
        assert _is_mimo_family("mimo-v2.5-asr") is True

    def test_non_mimo_not_detected(self):
        from services.chat.system_prompt import _is_mimo_family

        assert _is_mimo_family("gpt-4o") is False
        assert _is_mimo_family("claude-3-5-sonnet") is False
        assert _is_mimo_family("gemini-1.5-pro") is False

    def test_image_attachment_uses_image_url_part(self):
        from services.chat.persistence import _build_persisted_content
        from modules.system import ChatMessageRequest

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
        assert image_part["type"] == "image_url"
        assert image_part["image_url"]["url"] == "http://example.com/image.png"

    def test_no_attachments_returns_text(self):
        from services.chat.persistence import _build_persisted_content
        from modules.system import ChatMessageRequest

        req = SimpleNamespace(message=ChatMessageRequest(role="user", content="Just text"))
        content, content_type = _build_persisted_content(req)
        assert content_type == "text"
        assert content == "Just text"


# ── TestChatE2E (real MiMo API calls) ───────────────────────────────


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
    raw_data = b"\x00\xFF\x00\x00"
    compressed = zlib.compress(raw_data)
    idat_crc = zlib.crc32(b"IDAT" + compressed)
    idat = struct.pack(">I", len(compressed)) + b"IDAT" + compressed + struct.pack(">I", idat_crc & 0xFFFFFFFF)
    iend_crc = zlib.crc32(b"IEND")
    iend = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", iend_crc & 0xFFFFFFFF)
    return sig + ihdr + idat + iend


@pytest.mark.e2e
class TestChatE2E:
    def test_stateless_completion(self, test_client, test_token):
        """Test the actual /api/llm/completion endpoint without mocks."""
        headers = {"Authorization": f"Bearer {test_token}"}
        payload = {
            "messages": [{"role": "user", "content": "Say 'hello' in one word."}],
            "model": "mimo-v2.5-pro",
            "temperature": 0.5,
            "max_tokens": 50,
        }
        resp = test_client.post("/api/llm/completion", headers=headers, json=payload)
        assert resp.status_code == 200
        body = resp.json()
        assert body["content"] is not None
        assert len(body["content"]) > 0

    def test_websocket_chat_flow(self, test_client, test_token):
        """Test the actual WebSocket chat flow from session creation to prompt completion without mocks."""
        with test_client.websocket_connect(f"/api/chat/ws?token={test_token}") as ws:
            # 1. Create a new session
            ws.send_json({"jsonrpc": "2.0", "id": 1, "method": "session.create", "params": {}})
            resp = ws.receive_json()
            assert "result" in resp, f"Unexpected response: {resp}"
            session_id = resp["result"]["session_id"]
            assert session_id

            # 2. Submit a prompt
            ws.send_json({"jsonrpc": "2.0", "id": 2, "method": "prompt.submit", "params": {"session_id": session_id, "text": "Say 'ok' in one word."}})
            resp = ws.receive_json()
            assert resp["result"] == {"queued": True}

            # 3. Collect streaming events until message is complete
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

    def test_websocket_auth_rejection(self, test_client):
        """Test that the WebSocket connection is rejected with an invalid token."""
        from starlette.testclient import WebSocketDisconnect

        with pytest.raises((WebSocketDisconnect, Exception)):
            with test_client.websocket_connect("/api/chat/ws?token=invalid-token-abc"):
                pass

    def test_websocket_session_lifecycle(self, test_client, test_token):
        """Test creating a session, closing it, and verifying it is no longer usable."""
        with test_client.websocket_connect(f"/api/chat/ws?token={test_token}") as ws:
            # Create session
            ws.send_json({"jsonrpc": "2.0", "id": 1, "method": "session.create", "params": {}})
            resp = ws.receive_json()
            session_id = resp["result"]["session_id"]

            # Close session
            ws.send_json({"jsonrpc": "2.0", "id": 2, "method": "session.close", "params": {"session_id": session_id}})
            resp = ws.receive_json()
            assert resp["result"] == {}

            # Try to submit prompt to closed session
            ws.send_json({"jsonrpc": "2.0", "id": 3, "method": "prompt.submit", "params": {"session_id": session_id, "text": "hello"}})
            resp = ws.receive_json()
            assert "error" in resp
            assert "session not found" in resp["error"].get("message", "").lower()
