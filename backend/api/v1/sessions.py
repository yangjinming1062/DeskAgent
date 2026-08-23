from typing import Literal

from common import get_router
from components import SEARCH_INPUT_MAX_LEN, SESSION_PREVIEW_MAX_CHARS, SETTINGS, SQL_LIKE_ESCAPE_CHAR, attachments_gc_session, get_db, get_logger, temp_files_gc_session
from fastapi import Depends, HTTPException, Query
from modules.auth import LoginRecord, User, get_current_session
from modules.conversation import (
    Conversation,
    DesktopSessionInfo,
    DesktopSessionListResponse,
    DesktopSessionMessagesResponse,
    DesktopSessionPatchRequest,
    DesktopSessionSearchResponse,
    Message,
)
from services.chat import build_session_messages
from services.conversation import CRON_KIND, MAIN_KIND
from sqlalchemy import String, asc, cast, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

router = get_router(dependencies=[Depends(get_current_session)])

logger = get_logger(__name__)


# 列表/搜索预览关联子查询：取首条非空 user 消息并在 SQL 层截断，避免大文本/多模态造成整页 IO 放大
_preview_subquery = (
    select(func.substr(Message.content, 1, SESSION_PREVIEW_MAX_CHARS))
    .where(
        Message.conversation_id == Conversation.id,
        Message.role == "user",
        Message.content.isnot(None),
        Message.content != "",
    )
    .order_by(Message.id)
    .limit(1)
    .correlate(Conversation)
    .scalar_subquery()
    .label("preview")
)


def _conversation_to_session_info(conv: Conversation, *, msg_count: int, input_tok: int, output_tok: int, tool_count: int, preview: str | None) -> DesktopSessionInfo:
    has_real_parent = conv.parent_id is not None and conv.parent_id != conv.id

    return DesktopSessionInfo(
        id=str(conv.id),
        kind=conv.kind,
        title=conv.title,
        started_at=int(conv.created_at.timestamp() * 1000),
        last_active=int(conv.updated_at.timestamp() * 1000),
        message_count=msg_count,
        input_tokens=input_tok,
        output_tokens=output_tok,
        tool_call_count=tool_count,
        preview=preview,
        cwd=conv.cwd,
        archived=conv.parent_id == conv.id,
        lineage_root_id=str(conv.parent_id) if has_real_parent else None,
    )


async def _get_conversation_or_404(db: AsyncSession, user: User, session_id: str) -> Conversation:
    """按 id 查会话并强制归属校验，未命中抛 404。"""
    conv = await Conversation.by_session_id(db, session_id, user_id=user.id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return conv


@router.get("", response_model=DesktopSessionListResponse)
async def list_sessions(
    limit: int = 40,
    offset: int = 0,
    min_messages: int = 0,
    archived: Literal["only", "exclude", "include"] = "exclude",
    order: Literal["recent", "oldest"] = "recent",
    include_subagents: bool = False,
    current: tuple[User, LoginRecord] = Depends(get_current_session),
    db: AsyncSession = Depends(get_db),
) -> DesktopSessionListResponse:
    user, _ = current
    q = select(Conversation, _preview_subquery).where(Conversation.user_id == user.id, Conversation.kind != CRON_KIND)

    msg_stats = (
        select(
            Message.conversation_id,
            func.count(Message.id).label("msg_count"),
            func.coalesce(func.sum(Message.prompt_tokens), 0).label("input_tok"),
            func.coalesce(func.sum(Message.completion_tokens), 0).label("output_tok"),
        )
        .group_by(Message.conversation_id)
        .subquery()
    )

    tool_stats = select(Message.conversation_id, func.count(Message.id).label("tool_count")).where(Message.tool_calls.isnot(None)).group_by(Message.conversation_id).subquery()

    q = q.outerjoin(msg_stats, Conversation.id == msg_stats.c.conversation_id)
    q = q.outerjoin(tool_stats, Conversation.id == tool_stats.c.conversation_id)

    q = q.add_columns(msg_stats.c.msg_count, msg_stats.c.input_tok, msg_stats.c.output_tok, tool_stats.c.tool_count)

    if archived == "only":
        # 自指 parent_id 标记归档；parent_id 指向其他会话的是子代理，仍可见。
        q = q.where(Conversation.parent_id == Conversation.id)
    elif archived == "exclude":
        # 默认（include_subagents=False）仅 parent_id IS NULL 的顶层会话；自指归档行也被 is_(None) 排除。开启 include_subagents=True 显示主+子代理，但仍隐藏自指归档行。
        q = q.where(Conversation.parent_id.is_(None) | (Conversation.parent_id != Conversation.id)) if include_subagents else q.where(Conversation.parent_id.is_(None))
    # 其他 archived 取值由 Literal 在 HTTP 边界 422 拦截，此处 fallthrough 实际不可达，保留 no-op 供未来扩展。
    # include_subagents 仅作用于 archived="exclude"；"only"/"include" 路径忽略它（子代理导航走搜索端点与直链，不在 archived 切换 UI 内）。

    if min_messages > 0:
        q = q.where(func.coalesce(msg_stats.c.msg_count, 0) >= min_messages)

    total_q = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()

    q = q.order_by(desc(Conversation.updated_at)) if order == "recent" else q.order_by(asc(Conversation.created_at))

    rows = (await db.execute(q.offset(offset).limit(limit))).all()

    sessions = []
    for conv, preview, mc, it, ot, tc in rows:
        sessions.append(
            _conversation_to_session_info(
                conv,
                msg_count=int(mc or 0),
                input_tok=int(it or 0),
                output_tok=int(ot or 0),
                tool_count=int(tc or 0),
                preview=preview,
            ),
        )

    return DesktopSessionListResponse(limit=limit, offset=offset, total=total_q, sessions=sessions)


@router.get("/search", response_model=DesktopSessionSearchResponse)
async def search_sessions(
    q: str = Query(..., min_length=1, description="Substring to match against title, message content, and id"),
    current: tuple[User, object] = Depends(get_current_session),
    db: AsyncSession = Depends(get_db),
) -> DesktopSessionSearchResponse:
    """按标题、会话 id 或会话内任意消息检索；每用户最多 20 条（按最近活跃排序），q 必填且限长。"""
    user, _ = current

    # 限制搜索词长度——LIKE 对多 KB 字符串慢，无 UX 理由让用户搜 10k 字符。
    if len(q) > SEARCH_INPUT_MAX_LEN:
        q = q[:SEARCH_INPUT_MAX_LEN]
    # 转义 SQL LIKE 元字符，保证用户输入按字面量匹配。
    escaped = q.replace(SQL_LIKE_ESCAPE_CHAR, SQL_LIKE_ESCAPE_CHAR * 2).replace("%", f"{SQL_LIKE_ESCAPE_CHAR}%").replace("_", f"{SQL_LIKE_ESCAPE_CHAR}_")
    pattern = f"%{escaped}%"

    # 拆两条查询：标题/id 匹配 vs 包含匹配消息的会话；Python 端合并——内容扫描路径封顶独立会话 id，分两条读路径便于限流与可读性。
    title_match_ids = [
        row[0]
        for row in (
            await db.execute(
                select(Conversation.id).where(
                    Conversation.user_id == user.id,
                    Conversation.kind != CRON_KIND,
                    or_(Conversation.title.ilike(pattern, escape=SQL_LIKE_ESCAPE_CHAR), cast(Conversation.id, String).like(pattern, escape=SQL_LIKE_ESCAPE_CHAR)),
                ),
            )
        ).all()
    ]
    # 内容扫描是最贵路径（messages.content 全表 LIKE），封顶 200 个独立会话 id。
    content_match_ids = [
        row[0]
        for row in (
            await db.execute(
                select(Message.conversation_id)
                .where(Message.content.ilike(pattern, escape=SQL_LIKE_ESCAPE_CHAR))
                .join(Conversation, Conversation.id == Message.conversation_id)
                .where(Conversation.user_id == user.id, Conversation.kind != CRON_KIND)
                .distinct()
                .limit(200),
            )
        ).all()
    ]

    merged_ids = set(title_match_ids) | set(content_match_ids)
    if not merged_ids:
        return DesktopSessionSearchResponse(sessions=[])

    rows = (
        await db.execute(
            select(Conversation, _preview_subquery, func.count(Message.id).label("msg_count"))
            .outerjoin(Message, Message.conversation_id == Conversation.id)
            .where(Conversation.id.in_(merged_ids))
            .group_by(Conversation.id)
            .order_by(desc(Conversation.updated_at))
            .limit(20),
        )
    ).all()

    sessions = []
    for conv, preview, msg_count in rows:
        sessions.append(
            _conversation_to_session_info(
                conv,
                msg_count=int(msg_count or 0),
                input_tok=0,
                output_tok=0,
                tool_count=0,
                preview=preview,
            ),
        )

    return DesktopSessionSearchResponse(sessions=sessions)


@router.get("/{session_id}/messages", response_model=DesktopSessionMessagesResponse)
async def get_session_messages(
    session_id: str,
    before_id: int | None = Query(default=None, ge=0, description="取 id < before_id 的更早历史，配合 next_cursor 翻页"),
    limit: int | None = Query(default=None, ge=1, le=1000, description="单次返回条数上限"),
    current: tuple[User, object] = Depends(get_current_session),
    db: AsyncSession = Depends(get_db),
) -> DesktopSessionMessagesResponse:
    user, _ = current
    conv = await _get_conversation_or_404(db, user, session_id)
    result = await build_session_messages(conv.id, db, before_id=before_id, limit=limit)
    return DesktopSessionMessagesResponse(session_id=str(conv.id), messages=result)


@router.patch("/{session_id}")
async def patch_session(session_id: str, body: DesktopSessionPatchRequest, current: tuple[User, object] = Depends(get_current_session), db: AsyncSession = Depends(get_db)) -> dict:
    user, _ = current
    conv = await _get_conversation_or_404(db, user, session_id)
    if conv.kind == MAIN_KIND:
        raise HTTPException(status_code=403, detail="Main conversation cannot be modified or deleted")
    if body.title is not None:
        conv.title = body.title
    if body.archived is not None:
        conv.parent_id = conv.id if body.archived else None
    await db.commit()
    return {"ok": True}


@router.delete("/{session_id}")
async def delete_session(session_id: str, current: tuple[User, object] = Depends(get_current_session), db: AsyncSession = Depends(get_db)) -> dict:
    user, _ = current
    conv = await _get_conversation_or_404(db, user, session_id)
    if conv.kind == MAIN_KIND:
        raise HTTPException(status_code=403, detail="Main conversation cannot be modified or deleted")
    deleted_id = conv.id
    await db.delete(conv)
    await db.commit()
    # 级联清理远端模式附件，尽力而为——文件系统错误（权限、磁盘满）不能让已删除会话行残留，日志记录后吞掉；gc_session 校验 session_id 形态并拒绝路径穿越，rmtree 仅作用于 SETTINGS.data_dir/desktop-attachments/。
    try:
        attachments_gc_session(SETTINGS.data_dir, str(deleted_id))
    except Exception:
        logger.warning("attachments_gc_session failed for session %s", deleted_id, exc_info=True)
    try:
        temp_files_gc_session(str(deleted_id))
    except Exception:
        logger.warning("temp_files_gc failed for session %s", deleted_id, exc_info=True)
    return {"ok": True}
