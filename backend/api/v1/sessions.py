from typing import Literal

from common import get_router
from components import get_db
from components import SEARCH_INPUT_MAX_LEN
from components import SESSION_PREVIEW_MAX_CHARS
from components import SETTINGS
from components import SQL_LIKE_ESCAPE_CHAR
from core import attachments_gc_session as attachments_gc
from core import build_session_messages
from core import temp_files_gc_session as temp_files_gc
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from modules.auth import get_current_session
from modules.auth import User
from modules.conversation import Conversation
from modules.conversation import DesktopSessionInfo
from modules.conversation import DesktopSessionListResponse
from modules.conversation import DesktopSessionMessagesResponse
from modules.conversation import DesktopSessionPatchRequest
from modules.conversation import DesktopSessionSearchResponse
from modules.conversation import Message
from sqlalchemy import asc
from sqlalchemy import desc
from sqlalchemy import func
from sqlalchemy import or_
from sqlalchemy.orm import selectinload
from sqlalchemy.orm import Session

router = get_router(dependencies=[Depends(get_current_session)])

from components import get_logger

logger = get_logger(__name__)


def _conversation_to_session_info(conv: Conversation, msg_count: int, input_tok: int, output_tok: int, tool_count: int) -> DesktopSessionInfo:
    preview = None
    # ``conv.messages`` must be eagerly loaded by the caller (selectinload)
    # — otherwise iterating it here would issue N+1 SELECTs per row in
    # the list / search results.
    for msg in conv.messages:
        if msg.role == "user" and msg.content:
            preview = msg.content[:SESSION_PREVIEW_MAX_CHARS]
            break

    has_real_parent = conv.parent_id is not None and conv.parent_id != conv.id

    return DesktopSessionInfo(
        id=str(conv.id),
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


def _get_conversation_or_404(db: Session, user: User, session_id: str) -> Conversation:
    """Fetch a Conversation by id, enforcing ownership; raises 404 on miss."""
    conv = Conversation.by_session_id(db, session_id, user_id=user.id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return conv


@router.get("", response_model=DesktopSessionListResponse)
def list_sessions(
    limit: int = 40,
    offset: int = 0,
    min_messages: int = 0,
    archived: str = "exclude",
    order: Literal["recent", "oldest"] = "recent",
    include_subagents: bool = False,
    current: tuple[User, object] = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> DesktopSessionListResponse:
    user, _ = current
    # selectinload(Conversation.messages) so _conversation_to_session_info can
    # walk conv.messages to compute ``preview`` without an N+1 SELECT per row.
    q = db.query(Conversation).options(selectinload(Conversation.messages)).filter(Conversation.user_id == user.id)

    if archived == "only":
        # Self-referencing parent_id marks archived; real lineage (parent_id
        # pointing at a different conversation) is a subagent and stays visible.
        q = q.filter(Conversation.parent_id == Conversation.id)
    elif archived == "exclude":
        # Default (include_subagents=False): only top-level conversations —
        # rows where parent_id IS NULL. Subagents (parent_id pointing at a
        # different conversation) are hidden from the sidebar by default.
        # Self-archived rows (parent_id == self.id) are also excluded by
        # ``is_(None)``, so archived main conversations stay hidden here.
        # Opt-in (include_subagents=True): show main + subagents, still
        # hiding self-archived rows.
        if include_subagents:
            q = q.filter((Conversation.parent_id.is_(None)) | (Conversation.parent_id != Conversation.id))
        else:
            q = q.filter(Conversation.parent_id.is_(None))
    # Any other value: the Literal validator rejects unknown ``archived``
    # at the HTTP boundary with 422, so this fallthrough is unreachable in
    # practice — left as a no-op for future string inputs.
    # ``include_subagents`` only affects ``archived="exclude"``; the
    # ``"only"`` and ``"include"`` paths ignore it (self-archived rows and
    # all rows respectively), which is the intended semantics — subagent
    # navigation lives on the search endpoint and direct URL, not in the
    # ``archived`` toggle UI.

    # Subquery for message counts
    msg_stats = (
        db.query(
            Message.conversation_id,
            func.count(Message.id).label("msg_count"),
            func.coalesce(func.sum(Message.prompt_tokens), 0).label("input_tok"),
            func.coalesce(func.sum(Message.completion_tokens), 0).label("output_tok"),
        )
        .group_by(Message.conversation_id)
        .subquery()
    )

    tool_stats = (
        db.query(
            Message.conversation_id,
            func.count(Message.id).label("tool_count"),
        )
        .filter(Message.tool_calls.isnot(None))
        .group_by(Message.conversation_id)
        .subquery()
    )

    q = q.outerjoin(msg_stats, Conversation.id == msg_stats.c.conversation_id)
    q = q.outerjoin(tool_stats, Conversation.id == tool_stats.c.conversation_id)

    if min_messages > 0:
        q = q.filter(func.coalesce(msg_stats.c.msg_count, 0) >= min_messages)

    total_q = q.count()

    if order == "recent":
        q = q.order_by(desc(Conversation.updated_at))
    else:
        q = q.order_by(asc(Conversation.created_at))

    convs = q.offset(offset).limit(limit).all()

    sessions = []
    for conv in convs:
        mc = getattr(conv, "msg_count", None)
        it = getattr(conv, "input_tok", None)
        ot = getattr(conv, "output_tok", None)
        tc = getattr(conv, "tool_count", None)
        sessions.append(
            _conversation_to_session_info(
                conv,
                int(mc) if mc else 0,
                int(it) if it else 0,
                int(ot) if ot else 0,
                int(tc) if tc else 0,
            )
        )

    return DesktopSessionListResponse(
        limit=limit,
        offset=offset,
        total=total_q,
        sessions=sessions,
    )


@router.get("/search", response_model=DesktopSessionSearchResponse)
def search_sessions(
    q: str = Query(..., min_length=1, description="Substring to match against title, message content, and id"),
    current: tuple[User, object] = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> DesktopSessionSearchResponse:
    """Search sessions by title, conversation id, or any message in the conversation.

    Per-user, capped at 20 hits (most recently updated first). ``q`` is
    required (empty/missing returns 422) and length-capped.
    """
    user, _ = current

    # Cap the search term length; SQLite LIKE on multi-KB strings is slow
    # and there's no legitimate UX reason to search for 10k chars at once.
    if len(q) > SEARCH_INPUT_MAX_LEN:
        q = q[:SEARCH_INPUT_MAX_LEN]
    # Escape SQL LIKE metachars so user input is treated as a literal.
    escaped = q.replace(SQL_LIKE_ESCAPE_CHAR, SQL_LIKE_ESCAPE_CHAR * 2).replace("%", f"{SQL_LIKE_ESCAPE_CHAR}%").replace("_", f"{SQL_LIKE_ESCAPE_CHAR}_")
    pattern = f"%{escaped}%"

    # Two separate queries: conversations whose title/id matches, and
    # conversations that contain a matching message. Merge in Python to
    # avoid UNION subquery patterns that some dialects (SQLite) handle
    # poorly with nested subqueries.
    title_match_ids = [
        row[0]
        for row in db.query(Conversation.id)
        .filter(
            Conversation.user_id == user.id,
            or_(
                Conversation.title.ilike(pattern, escape=SQL_LIKE_ESCAPE_CHAR),
                Conversation.id.like(pattern, escape=SQL_LIKE_ESCAPE_CHAR),
            ),
        )
        .all()
    ]
    # The content scan is the most expensive path (full-table LIKE on
    # ``messages.content``). Cap at 200 distinct conversation ids.
    content_match_ids = [
        row[0]
        for row in db.query(Message.conversation_id)
        .filter(Message.content.ilike(pattern, escape=SQL_LIKE_ESCAPE_CHAR))
        .join(Conversation, Conversation.id == Message.conversation_id)
        .filter(Conversation.user_id == user.id)
        .distinct()
        .limit(200)
        .all()
    ]

    merged_ids = set(title_match_ids) | set(content_match_ids)
    if not merged_ids:
        return DesktopSessionSearchResponse(sessions=[])

    rows = (
        db.query(Conversation, func.count(Message.id).label("msg_count"))
        .options(selectinload(Conversation.messages))
        .outerjoin(Message, Message.conversation_id == Conversation.id)
        .filter(Conversation.id.in_(merged_ids))
        .group_by(Conversation.id)
        .order_by(desc(Conversation.updated_at))
        .limit(20)
        .all()
    )

    sessions = []
    for conv, msg_count in rows:
        sessions.append(_conversation_to_session_info(conv, int(msg_count or 0), 0, 0, 0))

    return DesktopSessionSearchResponse(sessions=sessions)


@router.get("/{session_id}/messages", response_model=DesktopSessionMessagesResponse)
def get_session_messages(
    session_id: str,
    current: tuple[User, object] = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> DesktopSessionMessagesResponse:
    user, _ = current
    conv = _get_conversation_or_404(db, user, session_id)
    result = build_session_messages(conv.id, db)
    return DesktopSessionMessagesResponse(session_id=str(conv.id), messages=result)


@router.patch("/{session_id}")
def patch_session(
    session_id: str,
    body: DesktopSessionPatchRequest,
    current: tuple[User, object] = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> dict:
    user, _ = current
    conv = _get_conversation_or_404(db, user, session_id)
    if body.title is not None:
        conv.title = body.title
    if body.archived is not None:
        conv.parent_id = conv.id if body.archived else None
    db.commit()
    return {"ok": True}


@router.delete("/{session_id}")
def delete_session(
    session_id: str,
    current: tuple[User, object] = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> dict:
    user, _ = current
    conv = _get_conversation_or_404(db, user, session_id)
    deleted_id = conv.id
    db.delete(conv)
    db.commit()
    # Cascade-cleanup of remote-mode attachments. Best-effort — a
    # filesystem failure (permissions, disk full) must not strand a deleted
    # session row, so we log and swallow. gc_session validates ``session_id``
    # shape and refuses path traversal; the underlying rmtree is bounded
    # by SETTINGS.data_dir/desktop-attachments/.
    try:
        attachments_gc(SETTINGS.data_dir, str(deleted_id))
    except Exception:
        logger.warning(
            "attachments_gc failed for session %s",
            deleted_id,
            exc_info=True,
        )
    # Also clean up temp media files for this session
    try:
        temp_files_gc(str(deleted_id))
    except Exception:
        logger.warning("temp_files_gc failed for session %s", deleted_id, exc_info=True)
    return {"ok": True}
