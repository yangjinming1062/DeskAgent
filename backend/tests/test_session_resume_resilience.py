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

    await dispatcher.push_event("message.delta", {"text": "hello"}, session_id="s1")
    await dispatcher.push_event("message.delta", {"text": " world"}, session_id="s1")
    assert len(replay_buffer) == 2
    assert replay_buffer.max_seq == 2

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

    async with SessionLocal() as db:
        conv = Conversation(id=1010, user_id=101, cwd="/test")
        db.add(conv)
        await db.commit()

    await dispatcher.push_event("message.start", {}, session_id="1010")
    await dispatcher.push_event("message.delta", {"text": "chunk1"}, session_id="1010")
    await dispatcher.push_event("message.delta", {"text": "chunk2"}, session_id="1010")

    sent_frames.clear()

    # 从 last_seq = 1 续传（客户端漏掉 seq 2/3）
    resume_res = await dispatcher._handlers["session.resume"](
        {"session_id": "1010", "last_seq": 1},
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

    # 给 capacity-2 buffer 推 4 个事件（seq 1、2 会被淘汰）
    for i in range(4):
        await dispatcher.push_event(
            "message.delta",
            {"text": str(i)},
            session_id="1020",
        )

    sent_frames.clear()

    # 客户端 last_seq = 1 已淘汰 → 回退到 DB 历史
    resume_res = await dispatcher._handlers["session.resume"](
        {"session_id": "1020", "last_seq": 1},
    )
    assert resume_res["resumed"] is False
    assert resume_res["replayed_count"] == 0
    assert "messages" in resume_res
    assert len(sent_frames) == 0  # WS 上无回放帧


@pytest.mark.asyncio
async def test_session_resume_server_restarted_ahead_seq(SessionLocal):
    sent_frames = []

    async def _send(frame):
        sent_frames.append(frame)

    # Server 已重启：ReplayBuffer 全新，current_seq = 0
    replay_buffer = ReplayBuffer()
    dispatcher = JsonRpcDispatcher(_send, replay_buffer=replay_buffer)
    runtime_sessions: dict[str, RuntimeSession] = {}

    user_session = UserGatewaySession(
        user_id=103,
        dispatcher=dispatcher,
        replay_buffer=replay_buffer,
        runtime_sessions=runtime_sessions,
        llm_config={"model_name": "test-model"},
    )

    _register_session_handlers(
        dispatcher,
        runtime_sessions,
        llm_config={"model_name": "test-model"},
        user_id=103,
        replay_buffer=replay_buffer,
        user_session=user_session,
    )

    async with SessionLocal() as db:
        conv = Conversation(id=1030, user_id=103, cwd="/test")
        db.add(conv)
        await db.commit()

    # 客户端带着 server 重启前的旧 last_seq=42 重连
    resume_res = await dispatcher._handlers["session.resume"](
        {"session_id": "1030", "last_seq": 42},
    )
    # Server 必须拒绝 replay（42 > 0），并以 current_seq=0 触发全量重载
    assert resume_res["resumed"] is False
    assert resume_res["current_seq"] == 0
    assert resume_res["replayed_count"] == 0
    assert "messages" in resume_res


@pytest.mark.asyncio
async def test_session_resume_hold_prevents_live_frame_reorder(SessionLocal):
    sent_frames = []

    async def _send(frame):
        sent_frames.append(frame)

    replay_buffer = ReplayBuffer()
    dispatcher = JsonRpcDispatcher(_send, replay_buffer=replay_buffer)
    runtime_sessions: dict[str, RuntimeSession] = {}

    user_session = UserGatewaySession(
        user_id=104,
        dispatcher=dispatcher,
        replay_buffer=replay_buffer,
        runtime_sessions=runtime_sessions,
        llm_config={"model_name": "test-model"},
    )

    _register_session_handlers(
        dispatcher,
        runtime_sessions,
        llm_config={"model_name": "test-model"},
        user_id=104,
        replay_buffer=replay_buffer,
        user_session=user_session,
    )

    async with SessionLocal() as db:
        conv = Conversation(id=1040, user_id=104, cwd="/test")
        db.add(conv)
        await db.commit()

    # 断开时 server 已发出 seq 1、2
    await dispatcher.push_event("chunk", {"text": "1"}, session_id="1040")
    await dispatcher.push_event("chunk", {"text": "2"}, session_id="1040")
    assert len(sent_frames) == 2
    sent_frames.clear()

    # Reconnect handshake 激活 hold
    dispatcher.enable_hold()

    # 后台任务在客户端尚未发 session.resume 时发出 live chunk 3
    await dispatcher.push_event("chunk", {"text": "3"}, session_id="1040")
    # Hold 生效：chunk 3 写入 buffer 为 seq 3，但不下发
    assert len(sent_frames) == 0
    assert replay_buffer.max_seq == 3

    # 客户端发 session.resume，last_seq=1（需要 seq 2、3）
    resume_res = await dispatcher._handlers["session.resume"](
        {"session_id": "1040", "last_seq": 1},
    )
    assert resume_res["resumed"] is True
    assert resume_res["replayed_count"] == 2
    # 回放帧必须按 seq 2、3 严格顺序
    assert len(sent_frames) == 2
    assert sent_frames[0]["params"]["seq"] == 2
    assert sent_frames[1]["params"]["seq"] == 3

    # resume 后 hold 释放，后续事件立刻下发
    await dispatcher.push_event("chunk", {"text": "4"}, session_id="1040")
    assert len(sent_frames) == 3
    assert sent_frames[2]["params"]["seq"] == 4


@pytest.mark.asyncio
async def test_session_resume_fallback_and_create_release_hold(SessionLocal):
    sent_frames = []

    async def _send(frame):
        sent_frames.append(frame)

    replay_buffer = ReplayBuffer(capacity=2)
    dispatcher = JsonRpcDispatcher(_send, replay_buffer=replay_buffer)
    runtime_sessions: dict[str, RuntimeSession] = {}

    user_session = UserGatewaySession(
        user_id=105,
        dispatcher=dispatcher,
        replay_buffer=replay_buffer,
        runtime_sessions=runtime_sessions,
        llm_config={"model_name": "test-model"},
    )

    _register_session_handlers(
        dispatcher,
        runtime_sessions,
        llm_config={"model_name": "test-model"},
        user_id=105,
        replay_buffer=replay_buffer,
        user_session=user_session,
    )

    async with SessionLocal() as db:
        conv = Conversation(id=1050, user_id=105, cwd="/test")
        db.add(conv)
        await db.commit()

    # handshake 时启用 hold
    dispatcher.enable_hold()

    # Fallback resume 路径：
    resume_res = await dispatcher._handlers["session.resume"](
        {"session_id": "1050", "last_seq": 999},
    )
    assert resume_res["resumed"] is False

    # hold 此时已被释放
    await dispatcher.push_event("chunk", {"text": "hello"}, session_id="1050")
    assert len(sent_frames) == 1
    assert sent_frames[0]["params"]["payload"]["text"] == "hello"

    # session.create 也会释放 hold
    sent_frames.clear()
    dispatcher.enable_hold()
    await dispatcher._handlers["session.create"]({})
    await dispatcher.push_event("chunk", {"text": "world"}, session_id="1050")
    assert len(sent_frames) == 1
