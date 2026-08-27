import asyncio

from components import get_logger

from ..base import ChannelAdapter, ChannelBindingSnapshot, InboundMessage, OnInbound
from ..registry import register

logger = get_logger(__name__)


class LoopbackAdapter(ChannelAdapter):
    """回环测试通道：无网络、由 REST 驱动的最小适配器，验证「入站 → im 回合 → 回复」全链路。

    run() 空转（等取消）占住守卫任务；deliver() 是 REST 的入站入口；send_text 只记录供测试断言。
    """

    channel_name = "loopback"
    conversation_title = "回环对话"
    can_initiate = True

    def __init__(self, snapshot: ChannelBindingSnapshot, on_inbound: OnInbound) -> None:
        super().__init__(snapshot, on_inbound)
        self.sent: list[tuple[str, str]] = []

    async def send_text(self, peer_id: str, text: str, context_token: str | None = None) -> None:
        self.sent.append((peer_id, text))
        logger.info("loopback send_text", extra={"binding": self.snapshot.id, "peer": peer_id, "chars": len(text)})

    async def deliver(self, msg: InboundMessage) -> asyncio.Future[str | None]:
        """REST 驱动的入站口：返回 bridge 的 per-message future（调用方决定等待多久）。"""
        return await self._on_inbound(msg)


register("loopback", LoopbackAdapter)
