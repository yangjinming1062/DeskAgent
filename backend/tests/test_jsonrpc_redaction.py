import pytest

from services.gateway import JsonRpcDispatcher, _redact_message


def test_redact_message_strips_paths_and_credentials():
    """P1-9: -32603 messages must never leak server-side internals — DB
    DSNs, absolute paths, traceback frames, known API-key prefixes, or
    Python exception class names from third-party libs."""
    leak = (
        "OperationalError: (psycopg2.OperationalError) connection to server at "
        "10.0.0.5 (10.0.0.5), port 5432 failed: FATAL: password authentication "
        'failed for user "postgres" /home/agent/DeskAgent/backend/services/chat/x.py:42'
    )
    redacted = _redact_message(leak)
    assert "OperationalError" not in redacted or "[redacted]" in redacted
    assert "/home/agent/" not in redacted
    assert "10.0.0.5" not in redacted
    assert "postgres" not in redacted
    assert "psycopg2" not in redacted or "[redacted]" in redacted


def test_redact_message_strips_traceback():
    msg = (
        "Something: Traceback (most recent call last):\n"
        '  File "/srv/app/backend/x.py", line 99, in foo\n'
        "    raise RuntimeError('boom')"
    )
    redacted = _redact_message(msg)
    assert "Traceback" not in redacted
    assert "/srv/app/" not in redacted
    assert "raise RuntimeError" not in redacted


def test_redact_message_strips_api_keys():
    msg = "openai call failed: sk-proj-abc123def456ghi789jkl012mno345pqr"
    redacted = _redact_message(msg)
    assert "sk-proj-abc" not in redacted
    assert "[redacted]" in redacted


def test_redact_message_strips_dsn():
    msg = "OperationalError: postgresql://app:s3cret@db.internal:5432/prod"
    redacted = _redact_message(msg)
    assert "s3cret" not in redacted
    assert "db.internal" not in redacted or "[redacted]" in redacted


def test_redact_message_caps_length():
    """Backstop: a runaway exception message can't blow up the WS frame."""
    msg = "x" * 5000
    redacted = _redact_message(msg)
    assert len(redacted) <= 512


def test_redact_message_keeps_clean_messages_intact():
    msg = "Method not found: foo.bar"
    assert _redact_message(msg) == msg


@pytest.mark.asyncio
async def test_jsonrpc_internal_error_redacts_handler_exception():
    """End-to-end: a handler raising a non-JsonRpcError returns -32603
    with a redacted message; full exception goes to the server log only."""
    import logging

    captured_frames: list[dict] = []

    async def _send(frame: dict) -> None:
        captured_frames.append(frame)

    dispatcher = JsonRpcDispatcher(_send)

    async def _boom(params: dict):
        raise RuntimeError(
            "connection to postgresql://app:s3cret@db:5432/x failed at /srv/app/main.py:10"
        )

    dispatcher.register("boom", _boom)

    previous_level = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        await dispatcher.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "boom", "params": {}}
        )
    finally:
        logging.disable(previous_level)

    assert len(captured_frames) == 1
    err = captured_frames[0]["error"]
    assert err["code"] == -32603
    assert "s3cret" not in err["message"]
    assert "postgresql://" not in err["message"]
    assert "/srv/app/" not in err["message"]
