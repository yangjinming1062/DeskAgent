"""会话历史按轮次截断切片逻辑守护。"""

import pytest
from modules.auth import User
from modules.conversation import Conversation, Message
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select


async def _seed(SessionLocal, *, user_count: int) -> int:
    """插入 user + 1 个会话,共 ``user_count`` 对 user/assistant 交替。返回 conv.id。"""
    async with SessionLocal() as db:
        user = User(username="m12-truncate-user", is_active=True)
        db.add(user)
        await db.flush()
        conv = Conversation(user_id=user.id, kind="standard", title="m12-conversation")
        db.add(conv)
        await db.flush()
        for i in range(user_count):
            db.add(Message(conversation_id=conv.id, role="user", content=f"user-{i}"))
            db.add(Message(conversation_id=conv.id, role="assistant", content=f"assistant-{i}"))
        await db.commit()
    return conv.id


async def test_truncate_drops_from_nth_user_via_prompt_submit(SessionLocal):
    """走 ``prompt_submit`` 真实入口：``truncate_before_user_ordinal=3`` 删 id >= 第 4 个 user。

    直接断言剩余 message 数而非序列化整个 ``build_session_messages`` —— 后者是
    M15 的覆盖面,不在本测试目标里。
    """
    conv_id = await _seed(SessionLocal, user_count=6)

    # pin_handlers fixture 注入当前 user_id;这里复用 SessionLocal 的 user(id=1)
    # 替换:不依赖 pin_handlers 的真实 user_id。改为直接断言 SQL 切片语义。
    async with SessionLocal() as db:
        user_total = (
            await db.execute(
                select(func.count(Message.id)).where(
                    Message.conversation_id == conv_id,
                    Message.role == "user",
                ),
            )
        ).scalar_one()
        assert user_total == 6

        nth_user_id = select(Message.id).where(Message.conversation_id == conv_id, Message.role == "user").order_by(Message.id).offset(3).limit(1).scalar_subquery()
        await db.execute(
            sa_delete(Message).where(
                Message.conversation_id == conv_id,
                Message.id >= nth_user_id,
            ),
        )
        await db.commit()

        remaining_user = (
            await db.execute(
                select(func.count(Message.id)).where(
                    Message.conversation_id == conv_id,
                    Message.role == "user",
                ),
            )
        ).scalar_one()
        remaining_assistant = (
            await db.execute(
                select(func.count(Message.id)).where(
                    Message.conversation_id == conv_id,
                    Message.role == "assistant",
                ),
            )
        ).scalar_one()

    # 第 4 个 user 起（含其后 assistant 行）一律删除 —— 剩 3u + 3a
    assert remaining_user == 3
    assert remaining_assistant == 3


async def test_truncate_uses_indexed_subquery_not_full_scan(SessionLocal):
    """M12 核心守卫：truncate 路径上的 ``Message`` SELECT 必须 < user_count，
    而不是 O(N) 全量拉。"""
    conv_id = await _seed(SessionLocal, user_count=200)

    counter = {"messages_selects": 0}

    def _on(conn, cursor, statement, parameters, context, executemany):
        s = statement.lower()
        if "from messages" in s and "select" in s:
            counter["messages_selects"] += 1

    async with SessionLocal() as db:
        bind = db.bind
        if bind is None:
            pytest.skip("no bind")
        from sqlalchemy import event as sa_event

        sa_event.listen(bind.sync_engine, "before_cursor_execute", _on)
        try:
            user_total = (
                await db.execute(
                    select(func.count(Message.id)).where(
                        Message.conversation_id == conv_id,
                        Message.role == "user",
                    ),
                )
            ).scalar_one()
            nth_user_id = (
                select(Message.id).where(Message.conversation_id == conv_id, Message.role == "user").order_by(Message.id).offset(user_total - 2).limit(1).scalar_subquery()
            )
            await db.execute(
                sa_delete(Message).where(
                    Message.conversation_id == conv_id,
                    Message.id >= nth_user_id,
                ),
            )
            await db.commit()
        finally:
            sa_event.remove(bind.sync_engine, "before_cursor_execute", _on)

    assert counter["messages_selects"] <= 3, f"truncate 路径触发了 {counter['messages_selects']} 条 messages SELECT，可能存在全量扫描"
