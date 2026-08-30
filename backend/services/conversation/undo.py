"""会话撤回：硬删除 ``id >= source_message_id`` 的全部行（含锚点本身），把锚点载荷推回客户端作为输入框草稿。与 ``fork`` 互为对偶——fork 在新会话复制 1..N，本服务在原会话硬删 N..end。"""

from modules.conversation import Conversation, Message
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.chat import build_session_messages
from services.conversation import SourceNotFoundError
from services.media import prune_videos_in_range

from .main_conversation import CRON_KIND, IM_KIND, SPECIAL_KIND


class UndoNotAllowedError(Exception):
    """会话 kind 不可撤回（special / im / cron）。"""


async def undo_conversation_to_message(
    db: AsyncSession,
    user_id: int,
    session_id: str,
    source_message_id: int,
) -> dict:
    """返回 ``{session_id, deleted_count, anchor: {text, content_type, media_json}, messages}``；抛出 ``UndoNotAllowedError`` / ``SourceNotFoundError``。"""
    conv = await Conversation.by_session_id(db, session_id, user_id=user_id)
    if conv is None:
        raise SourceNotFoundError(f"会话不存在或不属于当前用户: {session_id!r}")

    if conv.kind in (SPECIAL_KIND, IM_KIND, CRON_KIND):
        raise UndoNotAllowedError(f"该类型会话不可撤回 (kind={conv.kind!r})")

    anchor_row = (
        await db.execute(
            select(Message.role, Message.content, Message.content_type, Message.media_json).where(
                Message.id == source_message_id,
                Message.conversation_id == conv.id,
            ),
        )
    ).first()
    if anchor_row is None:
        raise SourceNotFoundError(
            f"锚点消息不在会话内: message_id={source_message_id} session_id={session_id!r}",
        )

    anchor_role, anchor_content, anchor_content_type, anchor_media_json = anchor_row
    if anchor_role != "user":
        raise UndoNotAllowedError(f"撤回仅支持 user-role 消息（source_message_id={source_message_id} 是 {anchor_role!r}）")

    deleted_count = (
        await db.execute(
            select(func.count(Message.id)).where(
                Message.conversation_id == conv.id,
                Message.id >= source_message_id,
            ),
        )
    ).scalar_one()

    # 顺序：先 GC 视频（prune 内部会改写 [lo, hi) 内行 part 占位），再硬删行。
    await prune_videos_in_range(db, conv.id, lo=source_message_id)

    await db.execute(
        delete(Message).where(
            Message.conversation_id == conv.id,
            Message.id >= source_message_id,
        ),
    )
    await db.commit()

    delivered = await build_session_messages(conv.id, db, include_id=True)

    return {
        "session_id": str(conv.id),
        "deleted_count": int(deleted_count),
        "anchor": {
            "text": anchor_content or "",
            "content_type": anchor_content_type or "text",
            "media_json": anchor_media_json,
        },
        "messages": delivered,
    }
