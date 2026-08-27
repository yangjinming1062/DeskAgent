import asyncio
import json

from common import get_or_404, get_router
from components import get_db, get_logger
from fastapi import Depends, HTTPException, Response, status
from modules.auth import User, get_current_session
from modules.channels import (
    BindingInfo,
    ChannelBinding,
    ChannelBindingPutRequest,
    ChannelCapabilities,
    ChannelInfo,
    ChannelListResponse,
    ChannelPeer,
    LoopbackInboundRequest,
    LoopbackInboundResponse,
    PeerActionRequest,
    PeerInfo,
    PeerListResponse,
)
from services.channels import MANAGER, InboundMessage, channels_info, try_resolve
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = get_router(dependencies=[Depends(get_current_session)])

logger = get_logger(__name__)

# 回环入站 REST 等待回合完成的窗口：覆盖一次常规 LLM 回合；超时后回合仍在跑（结果稍后落 im 会话历史）。
LOOPBACK_REPLY_WAIT_SECONDS = 120.0


async def _get_binding(db: AsyncSession, user: User, channel: str) -> ChannelBinding | None:
    return (await db.execute(select(ChannelBinding).where(ChannelBinding.user_id == user.id, ChannelBinding.channel == channel))).scalar_one_or_none()


async def _list_peers(db: AsyncSession, binding_id: int) -> PeerListResponse:
    peers = (await db.execute(select(ChannelPeer).where(ChannelPeer.binding_id == binding_id).order_by(ChannelPeer.status, ChannelPeer.id))).scalars()
    return PeerListResponse(items=[PeerInfo.model_validate(p) for p in peers])


@router.get("", response_model=ChannelListResponse)
async def list_channels(user: User = Depends(get_current_session), db: AsyncSession = Depends(get_db)) -> ChannelListResponse:
    """注册表能力位 + 当前用户各渠道绑定状态；凭据字段永不出现。"""
    bindings = {b.channel: b for b in (await db.execute(select(ChannelBinding).where(ChannelBinding.user_id == user.id))).scalars()}
    items = []
    for info in channels_info():
        binding = bindings.get(info["channel"])
        items.append(
            ChannelInfo(
                channel=info["channel"],
                title=info["title"],
                capabilities=ChannelCapabilities(**info["capabilities"]),
                binding=BindingInfo.model_validate(binding) if binding else None,
            ),
        )
    return ChannelListResponse(items=items)


@router.put("/{channel}", response_model=BindingInfo)
async def put_binding(
    channel: str,
    body: ChannelBindingPutRequest,
    user: User = Depends(get_current_session),
    db: AsyncSession = Depends(get_db),
) -> BindingInfo:
    """创建/更新渠道绑定（config 落 config_json）并重启适配器；需要登录的渠道（P1 微信）进入 login_pending。"""
    cls = try_resolve(channel)
    if cls is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown channel: {channel}")
    binding = await _get_binding(db, user, channel)
    if binding is None:
        binding = ChannelBinding(user_id=user.id, channel=channel, status="disabled", config_json=json.dumps(body.config, ensure_ascii=False))
        db.add(binding)
    else:
        binding.config_json = json.dumps(body.config, ensure_ascii=False)
    # 回环无登录态直连 connected；有登录流的渠道等扫码完成后由适配器转 connected。
    binding.status = "login_pending" if cls.requires_login else "connected"
    await db.commit()
    await db.refresh(binding)
    await MANAGER.restart_binding(user.id, channel)
    logger.info("channel binding upserted", extra={"user_id": user.id, "channel": channel, "status": binding.status})
    return BindingInfo.model_validate(binding)


@router.delete("/{channel}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_binding(
    channel: str,
    user: User = Depends(get_current_session),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """停用并删除绑定（peers 级联清除；im 会话行保留沉淀为历史，重建绑定时新开会话）。"""
    binding = await _get_binding(db, user, channel)
    if binding is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Binding not found")
    await db.delete(binding)
    await db.commit()
    await MANAGER.stop_binding(user.id, channel)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{channel}/peers", response_model=PeerListResponse)
async def list_peers(
    channel: str,
    user: User = Depends(get_current_session),
    db: AsyncSession = Depends(get_db),
) -> PeerListResponse:
    binding = await get_or_404(db, ChannelBinding, detail="Binding not found", user_id=user.id, channel=channel)
    return await _list_peers(db, binding.id)


@router.post("/{channel}/peers/{peer_id}", response_model=PeerListResponse)
async def act_on_peer(
    channel: str,
    peer_id: str,
    body: PeerActionRequest,
    user: User = Depends(get_current_session),
    db: AsyncSession = Depends(get_db),
) -> PeerListResponse:
    """对端审批：approve/block 改状态，delete 删行；返回操作后的全量列表便于前端就地刷新。"""
    binding = await get_or_404(db, ChannelBinding, detail="Binding not found", user_id=user.id, channel=channel)
    peer = await get_or_404(db, ChannelPeer, detail="Peer not found", binding_id=binding.id, peer_id=peer_id)
    if body.action == "delete":
        await db.delete(peer)
    else:
        peer.status = "allowed" if body.action == "approve" else "blocked"
    await db.commit()
    return await _list_peers(db, binding.id)


@router.post("/loopback/inbound", response_model=LoopbackInboundResponse)
async def loopback_inbound(
    body: LoopbackInboundRequest,
    user: User = Depends(get_current_session),
    db: AsyncSession = Depends(get_db),
) -> LoopbackInboundResponse:
    """回环测试驱动：模拟一条入站 IM 消息走完整桥接链路，返回伙伴回复（等待上限 LOOPBACK_REPLY_WAIT_SECONDS）。"""
    channel = "loopback"
    if try_resolve(channel) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="loopback channel not registered")
    binding = await _get_binding(db, user, channel)
    if binding is None:
        # 测试通道免 PUT：首次入站自动建绑定并拉起适配器。
        binding = ChannelBinding(user_id=user.id, channel=channel, status="connected", config_json="{}")
        db.add(binding)
        await db.commit()
        await db.refresh(binding)
    adapter = await MANAGER.wait_adapter(user.id, channel)
    if adapter is None:
        await MANAGER.restart_binding(user.id, channel)
        adapter = await MANAGER.wait_adapter(user.id, channel)
    if adapter is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="loopback adapter not running")
    future = await adapter.deliver(
        InboundMessage(channel=channel, peer_id=body.peer_id, peer_name=body.peer_name or body.peer_id, text=body.text),
    )
    try:
        reply = await asyncio.wait_for(future, timeout=LOOPBACK_REPLY_WAIT_SECONDS)
    except TimeoutError:
        return LoopbackInboundResponse(reply=None, queued=True)
    return LoopbackInboundResponse(reply=reply, queued=False)
