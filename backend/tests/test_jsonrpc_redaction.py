import pytest
from services.gateway import JsonRpcDispatcher, redact_message


def testredact_message_strips_paths_and_credentials():
    """P1-9：-32603 消息不得泄露 DSN/绝对路径/traceback/已知 API key 前缀或第三方异常类名。"""
    leak = (
        "OperationalError: (psycopg2.OperationalError) connection to server at "
        "10.0.0.5 (10.0.0.5), port 5432 failed: FATAL: password authentication "
        'failed for user "postgres" /home/agent/SpiritAgent/backend/services/chat/x.py:42'
    )
    redacted = redact_message(leak)
    assert "OperationalError" not in redacted
    assert "/home/agent/" not in redacted
    assert "10.0.0.5" not in redacted
    assert "postgres" not in redacted
    assert "psycopg2" not in redacted


def testredact_message_strips_traceback():
    msg = "Something: Traceback (most recent call last):\n  File \"/srv/app/backend/x.py\", line 99, in foo\n    raise RuntimeError('boom')"
    redacted = redact_message(msg)
    assert "Traceback" not in redacted
    assert "/srv/app/" not in redacted
    assert "raise RuntimeError" not in redacted


def testredact_message_strips_api_keys():
    msg = "openai call failed: sk-proj-abc123def456ghi789jkl012mno345pqr"
    redacted = redact_message(msg)
    assert "sk-proj-abc" not in redacted
    assert "[redacted]" in redacted


def testredact_message_strips_dsn():
    msg = "OperationalError: postgresql://app:s3cret@db.internal:5432/prod"
    redacted = redact_message(msg)
    assert "s3cret" not in redacted
    assert "db.internal" not in redacted


def testredact_message_caps_length():
    """保底：异常消息过长时裁剪，避免撑爆 WS 帧。"""
    msg = "x" * 5000
    redacted = redact_message(msg)
    assert len(redacted) <= 512


@pytest.mark.asyncio
async def test_jsonrpc_internal_error_redacts_handler_exception():
    """端到端：handler 抛非 JsonRpcError 时返回 -32603 且消息已脱敏，完整异常仅入服务端日志。"""
    import logging

    captured_frames: list[dict] = []

    async def _send(frame: dict) -> None:
        captured_frames.append(frame)

    dispatcher = JsonRpcDispatcher(_send)

    async def _boom(params: dict):
        raise RuntimeError(
            "connection to postgresql://app:s3cret@db:5432/x failed at /srv/app/main.py:10",
        )

    dispatcher.register("boom", _boom)

    previous_level = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        await dispatcher.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "boom", "params": {}},
        )
    finally:
        logging.disable(previous_level)

    assert len(captured_frames) == 1
    err = captured_frames[0]["error"]
    assert err["code"] == -32603
    assert "s3cret" not in err["message"]
    assert "postgresql://" not in err["message"]
    assert "/srv/app/" not in err["message"]


@pytest.mark.asyncio
async def test_push_error_event_redacts_raw_exception():
    """push_event 绕过 _reply_error，错误事件通道必须走同一脱敏流水线以免原始 DSN/traceback 触达渲染端。"""
    captured_frames: list[dict] = []

    async def _send(frame: dict) -> None:
        captured_frames.append(frame)

    dispatcher = JsonRpcDispatcher(_send)
    await dispatcher.push_error_event(
        "OperationalError: postgresql://app:s3cret@db:5432/x failed at /srv/app/main.py:10",
        session_id="42",
    )

    params = captured_frames[0]["params"]
    assert params["type"] == "error"
    assert "s3cret" not in params["payload"]["message"]
    assert "postgresql://" not in params["payload"]["message"]


def test_avatar_generation_error_str_is_curated():
    """str(exc) 会进入 502 reason 与 WS 载荷，原始 provider 错误只应留在 .internal。"""
    from services.companion import AvatarGenerationError

    exc = AvatarGenerationError(
        "image-gen provider failed",
        internal="httpx.ConnectError: https://provider.internal/v1 timeout",
    )
    assert str(exc) == "image-gen provider failed"
    assert "provider.internal" in exc.internal
    assert "provider.internal" not in str(AvatarGenerationError("safe"))
