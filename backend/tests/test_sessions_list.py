"""会话列表与搜索查询性能守护。"""

from datetime import timedelta

from api.v1.sessions import _preview_subquery
from components import utc_now
from modules.auth import User
from modules.conversation import Conversation, Message
from sqlalchemy import desc, event, select


class _StatementCounter:
    def __init__(self) -> None:
        self.messages_selects = 0

    def _on(self, conn, cursor, statement, parameters, context, executemany):
        s = statement.lower()
        if "from messages" in s or "messages " in s and "select" in s:
            self.messages_selects += 1


async def _seed(SessionLocal, conversations: int, messages_per_conv: int):
    now = utc_now()
    async with SessionLocal() as db:
        user = User(username="list-test-user", is_active=True)
        db.add(user)
        await db.flush()
        created = []
        for c in range(conversations):
            conv = Conversation(user_id=user.id, kind="standard", title=f"session-{c}")
            db.add(conv)
            await db.flush()
            for m in range(messages_per_conv):
                role = "user" if m % 2 == 0 else "assistant"
                db.add(
                    Message(
                        conversation_id=conv.id,
                        role=role,
                        content=f"msg-{c}-{m}",
                    ),
                )
            created.append(conv)
            conv.updated_at = now - timedelta(seconds=c)
        await db.commit()
    return user.id, [c.id for c in created]


async def test_list_sessions_preview_takes_first_user_message(SessionLocal):
    uid, conv_ids = await _seed(SessionLocal, conversations=3, messages_per_conv=4)

    async with SessionLocal() as db:
        rows = (
            await db.execute(
                select(Conversation, _preview_subquery).where(Conversation.user_id == uid, Conversation.kind != "cron").order_by(Conversation.id).limit(40),
            )
        ).all()

    assert len(rows) == 3
    previews = {str(r[0].id): r[1] for r in rows}
    for cid in conv_ids:
        assert previews[str(cid)] == f"msg-{conv_ids.index(cid)}-0"


async def test_list_sessions_does_not_n_plus_1_select(SessionLocal):
    user_id, _ = await _seed(SessionLocal, conversations=10, messages_per_conv=50)

    counter = _StatementCounter()

    async with SessionLocal() as db:
        bind = db.bind
        if bind is None:
            return
        sync_engine = bind.sync_engine
        event.listen(sync_engine, "before_cursor_execute", counter._on)
        try:
            rows = (
                await db.execute(
                    select(Conversation, _preview_subquery).where(Conversation.user_id == user_id, Conversation.kind != "cron").order_by(desc(Conversation.updated_at)).limit(40),
                )
            ).all()
        finally:
            event.remove(sync_engine, "before_cursor_execute", counter._on)

    assert len(rows) == 10
    assert counter.messages_selects < 5
