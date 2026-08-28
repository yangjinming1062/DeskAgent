import asyncio
import json
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from components import SETTINGS, get_logger, session_scope
from modules.channels import ChannelBinding, ChannelPeer
from modules.ws import WSEvent
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .base import ChannelAdapter, InboundMessage
from .conversation import get_or_create_channel_conversation
from .formatting import chunk_text, strip_markdown

logger = get_logger(__name__)

# 未知对端的首条消息触发的固定配对回复（pending 态只发一次）；放行由主人在 Hub / REST 审批。
PAIRING_NOTICE = "我还不认识你哦～已经请伙伴的主人确认了，批准之后我们再聊吧！"

# 回合已产出但渠道投递全失败时的兜底提示：不静默，让对端知道伙伴方才说话了。
_DELIVERY_FAILED_FALLBACK = "（伙伴刚想说话，但消息没能送达到这个渠道，请稍后再试）"

# 入站闸门串行化：state 读写与单飞行转移必须原子。
_STATE_LOCK = asyncio.Lock()


@dataclass
class _QueuedMessage:
    msg: InboundMessage
    future: asyncio.Future[str | None]


@dataclass
class _ChannelState:
    """每绑定一个桥接状态：单飞行锁 + 排队队列（回合进行中到达的消息合并进后续回合的前导批）。"""

    in_flight: bool = False
    queue: deque[_QueuedMessage] = field(default_factory=deque)
    # peer_id → 时间戳 deque：进程内每分钟滑动窗，渠道侧无频控前的成本护栏。
    rate_window: dict[str, deque[float]] = field(default_factory=dict)


_STATES: dict[int, _ChannelState] = {}


def _state_for(binding_id: int) -> _ChannelState:
    return _STATES.setdefault(binding_id, _ChannelState())


def _rate_exceeded(state: _ChannelState, peer_id: str) -> bool:
    now = asyncio.get_running_loop().time()
    window = state.rate_window.setdefault(peer_id, deque())
    while window and now - window[0] > 60:
        window.popleft()
    if len(window) >= SETTINGS.channels_inbound_rate_per_minute:
        return True
    window.append(now)
    return False


def _resolved(value: str | None) -> asyncio.Future[str | None]:
    fut: asyncio.Future[str | None] = asyncio.get_running_loop().create_future()
    fut.set_result(value)
    return fut


async def _get_peer(db: AsyncSession, binding_id: int, peer_id: str) -> ChannelPeer | None:
    return (await db.execute(select(ChannelPeer).where(ChannelPeer.binding_id == binding_id, ChannelPeer.peer_id == peer_id))).scalar_one_or_none()


async def _emit_peer_request(user_id: int, channel: str, msg: InboundMessage) -> None:
    """待审批对端事件走 outbox（桌面离线暂存重投），驱动 Hub/通知侧的审批入口。"""
    async with session_scope() as db:
        db.add(
            WSEvent(
                user_id=user_id,
                event_type="channel.peer_request",
                payload=json.dumps(
                    {"channel": channel, "peer_id": msg.peer_id, "peer_name": msg.peer_name, "preview": msg.text[:64]},
                    ensure_ascii=False,
                ),
            ),
        )
        await db.commit()


async def handle_inbound(adapter: ChannelAdapter, msg: InboundMessage) -> asyncio.Future[str | None]:
    """入站闸门：白名单检查 → 主动状态联动 → 单飞行入队；返回 per-message future（回合完成时以回复 resolve）。

    future 由调用方决定等待方式：REST 端点（用于无外部 IM 接入时的链路验证）await 到拿回复；轮询型适配器（微信 iLink）fire-and-forget。
    """
    snapshot = adapter.snapshot
    async with session_scope() as db:
        binding = await db.get(ChannelBinding, snapshot.id)
        if binding is None:
            return _resolved(None)
        peer = await _get_peer(db, binding.id, msg.peer_id)
        first_pending = peer is None
        if peer is None:
            peer = ChannelPeer(binding_id=binding.id, peer_id=msg.peer_id, peer_name=msg.peer_name, status="pending")
            db.add(peer)
        else:
            peer.peer_name = msg.peer_name or peer.peer_name
        peer.last_message_at = datetime.now(UTC)
        try:
            await db.commit()
        except IntegrityError:
            # 并发首条同 peer：判负方回滚后重读胜者行，按既有对端继续走闸门（配对回复只由胜者发一次）。
            await db.rollback()
            peer = await _get_peer(db, binding.id, msg.peer_id)
            if peer is None:
                return _resolved(None)
            first_pending = False
        peer_status = peer.status

    if peer_status != "allowed":
        if peer_status == "pending" and first_pending:
            # 默认拒绝 + 一次性配对提示：既不让陌生人消耗 LLM 回合，也不完全冷拒。
            try:
                await adapter.send_text(msg.peer_id, PAIRING_NOTICE, msg.context_token)
            except Exception:
                logger.exception("pairing notice delivery failed", extra={"binding": snapshot.id, "peer": msg.peer_id})
            await _emit_peer_request(snapshot.user_id, snapshot.channel, msg)
            return _resolved(PAIRING_NOTICE)
        return _resolved(None)

    from services.conversation import note_user_contact, reset_user_outreach

    # IM 侧的用户消息同样终结主动外联节奏、刷新接触计时——与 prompt.submit 同一契约。
    reset_user_outreach(snapshot.user_id)
    note_user_contact(snapshot.user_id)

    state = _state_for(snapshot.id)
    if _rate_exceeded(state, msg.peer_id):
        logger.warning("inbound rate limit exceeded, dropping", extra={"binding": snapshot.id, "peer": msg.peer_id})
        return _resolved(None)

    item = _QueuedMessage(msg=msg, future=asyncio.get_running_loop().create_future())
    async with _STATE_LOCK:
        if state.in_flight:
            if len(state.queue) >= SETTINGS.channels_turn_queue_max:
                dropped = state.queue.popleft()
                if not dropped.future.done():
                    dropped.future.set_result(None)
                logger.warning("turn queue full, dropped oldest", extra={"binding": snapshot.id})
            state.queue.append(item)
        else:
            state.in_flight = True
            asyncio.create_task(_run_turn(adapter, state, [item]))
    return item.future


class ChannelTurnEmitter:
    """无头回合发射器：捕获终端帧（message.complete 的文本 + affect），typing 时机转发给适配器。

    实现 services/chat 的 Emitter 协议（send_json）；帧全部留存便于调试，不做 WS 翻译——
    桌面端不实时旁观 IM 回合（P3 再议），历史经 im 会话 REST 读取。
    """

    def __init__(self) -> None:
        self.frames: list[dict] = []
        self.reply_text: str | None = None
        self.affect_emotion: str | None = None
        self.error: str | None = None
        self.media: list[dict] = []
        self._on_start: Callable[[], Awaitable[None]] | None = None

    def bind_typing(self, on_start: Callable[[], Awaitable[None]]) -> None:
        self._on_start = on_start

    async def send_json(self, data: dict) -> None:
        self.frames.append(data)
        frame_type = data.get("type")
        if frame_type == "message.start" and self._on_start is not None:
            asyncio.create_task(self._on_start())
        elif frame_type == "message.complete":
            self.reply_text = data.get("text")
            self.affect_emotion = (data.get("affect") or {}).get("emotion")
            new_media = data.get("media")
            if isinstance(new_media, list):
                self.media.extend(m for m in new_media if isinstance(m, dict))
        elif frame_type == "error":
            self.error = data.get("message")


async def _run_turn(adapter: ChannelAdapter, state: _ChannelState, batch: list[_QueuedMessage]) -> None:
    """执行一轮 im 回合并投递回复；结束后接管排队消息（整批合并为下一轮前导）或释放单飞行锁。"""
    snapshot = adapter.snapshot
    try:
        reply = await _execute_im_turn(adapter, [item.msg for item in batch])
        for item in batch:
            if not item.future.done():
                item.future.set_result(reply)
    except Exception:
        logger.exception("channel turn failed", extra={"binding": snapshot.id, "channel": snapshot.channel})
        for item in batch:
            if not item.future.done():
                item.future.set_result(None)
    finally:
        async with _STATE_LOCK:
            if state.queue:
                next_batch = list(state.queue)
                state.queue.clear()
                asyncio.create_task(_run_turn(adapter, state, next_batch))
            else:
                state.in_flight = False


async def _execute_im_turn(adapter: ChannelAdapter, batch: list[InboundMessage]) -> str | None:
    """跑一轮完整 chat turn（自带 emitter，不依赖用户 WS——桌面离线也能回），把回复格式化后经渠道送出。

    沿 _execute_cron_turn 的回合先例（connection.py），差异在 emitter：cron 复用用户 WS 派发器（桌面离线
    回合即死），这里无头捕获 + 回合后渠道投递。人设/长期记忆/主动记忆块按 user 加载，与桌面回合共享。
    """
    # 延迟导入避免 channels → chat/companion 的 eager import 环（chat 侧 import gateway，companion 侧重量级）。
    from modules.auth import ChatRequestClientContext
    from modules.system import ChatMessageRequest, ChatRequest

    from services.chat import load_user_settings, persist_extra_user_messages, run_chat_turn
    from services.companion.affect_emit import emit_companion_affect
    from services.llm import resolve_user_llm_config

    snapshot = adapter.snapshot
    last = batch[-1]
    async with session_scope() as db:
        binding = await db.get(ChannelBinding, snapshot.id)
        if binding is None:
            return None
        from .registry import resolve as _resolve_channel

        conv = await get_or_create_channel_conversation(db, binding, title_override=_resolve_channel(binding.channel).conversation_title)
        llm_config = await resolve_user_llm_config(db, snapshot.user_id)
        user_settings = await load_user_settings(db, snapshot.user_id)
        session_id = str(conv.id)
        # 排队合并：前导消息先落库（与 prompt.submit 的 batch 前导同构），末条驱动本轮。
        if len(batch) > 1:
            await persist_extra_user_messages(db, conv.id, [{"text": m.text, "attachments": []} for m in batch[:-1]])

    emitter = ChannelTurnEmitter()
    if adapter.supports_typing:

        async def _typing_on() -> None:
            try:
                await adapter.send_typing(last.peer_id, last.context_token, status=1)
            except Exception:
                logger.debug("typing indicator failed", extra={"binding": snapshot.id})

        emitter.bind_typing(_typing_on)

    client_context = ChatRequestClientContext(platform_hints=adapter.platform_hint()) if adapter.platform_hint() else None
    base = SETTINGS.public_base_url.strip().rstrip("/")
    attachments = []
    for a in last.attachments:
        url = a.url
        # temp-media 路径拼绝对 URL：与 video 上传接口同款（attachment_video_url），LLM 供应商按 image_url 字段拉取。
        if url.startswith("/api/media/files/") and base:
            url = f"{base}{url}"
        attachments.append({"type": a.type, "file_url": url, **({"name": a.name} if a.name else {})})
    req = ChatRequest(
        session_id=session_id,
        message=ChatMessageRequest(role="user", content=last.text, attachments=attachments or None),
        client_context=client_context,
    )
    try:
        await run_chat_turn(req, llm_config, user_settings, snapshot.user_id, emitter)
    except Exception:
        logger.exception("im chat turn crashed", extra={"binding": snapshot.id, "channel": snapshot.channel})
        return None

    if emitter.affect_emotion and emitter.affect_emotion != "neutral":
        # IM 回合的情绪镜像到桌面精灵：复用 companion.affect 事件（outbox，离线暂存重投）。
        try:
            await emit_companion_affect(snapshot.user_id, emitter.affect_emotion)
        except Exception:
            logger.exception("affect mirror failed", extra={"binding": snapshot.id})

    if emitter.error:
        logger.warning("im turn ended with error frame", extra={"binding": snapshot.id, "error": emitter.error})
        return None
    if not (emitter.reply_text or "").strip():
        # 纯肢体语言回合（无文本）在 IM 上无内容可送；情绪已镜像到桌面。
        return None

    plain = strip_markdown(emitter.reply_text)
    delivered = False
    chunks = chunk_text(plain, SETTINGS.weixin_reply_max_chars)
    media = emitter.media
    if media:
        # 媒体合并到第一条 chunk（同一条 iLink sendmessage）；若失败则后续 chunk 降级为纯文本。
        head = chunks[0] if chunks else None
        try:
            await adapter.send_media(last.peer_id, head, media, last.context_token)
            delivered = True
            for chunk in chunks[1:]:
                try:
                    await adapter.send_text(last.peer_id, chunk, last.context_token)
                    delivered = True
                except Exception:
                    logger.exception("reply chunk delivery failed", extra={"binding": snapshot.id, "channel": snapshot.channel})
        except Exception:
            logger.exception("media reply delivery failed; falling back to text", extra={"binding": snapshot.id, "channel": snapshot.channel})
            for chunk in chunks:
                try:
                    await adapter.send_text(last.peer_id, chunk, last.context_token)
                    delivered = True
                except Exception:
                    logger.exception("reply delivery failed", extra={"binding": snapshot.id, "channel": snapshot.channel})
    else:
        for chunk in chunks:
            try:
                await adapter.send_text(last.peer_id, chunk, last.context_token)
                delivered = True
            except Exception:
                logger.exception("reply delivery failed", extra={"binding": snapshot.id, "channel": snapshot.channel})
    if not delivered:
        try:
            await adapter.send_text(last.peer_id, _DELIVERY_FAILED_FALLBACK, last.context_token)
        except Exception:
            logger.error("fallback delivery also failed", extra={"binding": snapshot.id})
    return plain
