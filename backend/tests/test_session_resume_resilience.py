import pytest
from modules.conversation import Conversation
from services.gateway.buffer import ReplayBuffer
from services.gateway.handlers import (
    UserGatewaySession,
    _register_session_handlers,
)
from services.gateway.jsonrpc import JsonRpcDispatcher
from services.gateway.runtime import RuntimeSession


@pytest.mark.asyncio
async def test_session_ack_handler():
    sent_frames = []

    async def _send(frame):
        sent_frames.append(frame)

    replay_buffer = ReplayBuffer()
    dispatcher = JsonRpcDispatcher(_send, replay_buffer=replay_buffer)
    runtime_sessions: dict[str, RuntimeSession] = {}

    user_session = UserGatewaySession(
        user_id=100,
        dispatcher=dispatcher,
        replay_buffer=replay_buffer,
        runtime_sessions=runtime_sessions,
    )

    _register_session_handlers(
        dispatcher,
        runtime_sessions,
        llm_config={},
        user_id=100,
        replay_buffer=replay_buffer,
        user_session=user_session,
    )

    # Push some events to buffer
    await dispatcher.push_event("message.delta", {"text": "hello"}, session_id="s1")
    await dispatcher.push_event("message.delta", {"text": " world"}, session_id="s1")
    assert len(replay_buffer) == 2
    assert replay_buffer.max_seq == 2

    # Send session.ack for seq 1
    ack_res = await dispatcher._handlers["session.ack"]({"seq": 1})
    assert ack_res == {"acked": 1, "pruned": 1}
    assert len(replay_buffer) == 1
    assert replay_buffer.min_seq == 2


@pytest.mark.asyncio
async def test_session_resume_incremental_replay(SessionLocal, monkeypatch):
    sent_frames = []

    async def _send(frame):
        sent_frames.append(frame)

    replay_buffer = ReplayBuffer()
    dispatcher = JsonRpcDispatcher(_send, replay_buffer=replay_buffer)
    runtime_sessions: dict[str, RuntimeSession] = {}

    user_session = UserGatewaySession(
        user_id=101,
        dispatcher=dispatcher,
        replay_buffer=replay_buffer,
        runtime_sessions=runtime_sessions,
        llm_config={"model_name": "test-model"},
    )

    _register_session_handlers(
        dispatcher,
        runtime_sessions,
        llm_config={"model_name": "test-model"},
        user_id=101,
        replay_buffer=replay_buffer,
        user_session=user_session,
    )

    # Create dummy conversation in db
    async with SessionLocal() as db:
        conv = Conversation(id=1010, user_id=101, cwd="/test")
        db.add(conv)
        await db.commit()

    # Emit 3 events
    await dispatcher.push_event("message.start", {}, session_id="1010")
    await dispatcher.push_event("message.delta", {"text": "chunk1"}, session_id="1010")
    await dispatcher.push_event("message.delta", {"text": "chunk2"}, session_id="1010")

    sent_frames.clear()

    # Resume from last_seq = 1 (client missed seq 2 and 3)
    resume_res = await dispatcher._handlers["session.resume"](
        {"session_id": "1010", "last_seq": 1}
    )
    assert resume_res["resumed"] is True
    assert resume_res["replayed_count"] == 2
    assert resume_res["current_seq"] == 3
    assert len(sent_frames) == 2
    assert sent_frames[0]["params"]["seq"] == 2
    assert sent_frames[1]["params"]["seq"] == 3


@pytest.mark.asyncio
async def test_session_resume_fallback_on_expired_seq(SessionLocal):
    sent_frames = []

    async def _send(frame):
        sent_frames.append(frame)

    replay_buffer = ReplayBuffer(capacity=2)
    dispatcher = JsonRpcDispatcher(_send, replay_buffer=replay_buffer)
    runtime_sessions: dict[str, RuntimeSession] = {}

    user_session = UserGatewaySession(
        user_id=102,
        dispatcher=dispatcher,
        replay_buffer=replay_buffer,
        runtime_sessions=runtime_sessions,
        llm_config={"model_name": "test-model"},
    )

    _register_session_handlers(
        dispatcher,
        runtime_sessions,
        llm_config={"model_name": "test-model"},
        user_id=102,
        replay_buffer=replay_buffer,
        user_session=user_session,
    )

    async with SessionLocal() as db:
        conv = Conversation(id=1020, user_id=102, cwd="/test")
        db.add(conv)
        await db.commit()

    # Push 4 events into capacity-2 buffer (seq 1 and 2 are evicted)
    for i in range(4):
        await dispatcher.push_event(
            "message.delta", {"text": str(i)}, session_id="1020"
        )

    sent_frames.clear()

    # Client asks for last_seq = 1 which was evicted -> fallback to DB history
    resume_res = await dispatcher._handlers["session.resume"](
        {"session_id": "1020", "last_seq": 1}
    )
    assert resume_res["resumed"] is False
    assert resume_res["replayed_count"] == 0
    assert "messages" in resume_res
    assert len(sent_frames) == 0  # No frames replayed over WS
