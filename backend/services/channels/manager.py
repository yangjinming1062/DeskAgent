import asyncio
import contextlib
import json

from components import SETTINGS, get_logger, session_scope
from modules.channels import ChannelBinding
from modules.ws import WSEvent
from sqlalchemy import select

from .adapters.loopback import LoopbackAdapter  # noqa: F401 — 注册副作用：loopback 自注册进注册表
from .base import ChannelAdapter, ChannelBindingSnapshot, ChannelError, InboundMessage, OnInbound
from .bridge import handle_inbound
from .registry import resolve

logger = get_logger(__name__)


class ChannelManager:
    """绑定任务的进程内生命周期管理：启动加载 + REST 触发启停 + 每绑定守卫重试。

    无周期对账循环——REST 变更直接调 start/stop_binding，任务死亡由守卫循环自愈（非 fatal 退避重建）；
    单 web 进程语义（backend/README §6）下不需要 omp-wechat 那套端口单例锁/failover。
    """

    def __init__(self) -> None:
        # 强引用防 GC（沿 connection.py 的任务持有注释）；键 (user_id, channel) 与绑定唯一约束对齐。
        self._tasks: dict[tuple[int, str], asyncio.Task] = {}
        self._adapters: dict[tuple[int, str], ChannelAdapter] = {}

    def adapter(self, user_id: int, channel: str) -> ChannelAdapter | None:
        return self._adapters.get((user_id, channel))

    async def wait_adapter(self, user_id: int, channel: str, timeout: float = 5.0) -> ChannelAdapter | None:
        """等待守卫任务完成适配器构造（create_task 到首段执行有调度延迟）；超时返回 None 由调用方决定重启或报错。"""
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            adapter = self._adapters.get((user_id, channel))
            if adapter is not None:
                return adapter
            await asyncio.sleep(0.05)
        return None

    async def load_and_start(self) -> None:
        """启动路径：拉起所有未停用的绑定（lifespan 调用，幂等——restart 先停旧任务）。"""
        async with session_scope() as db:
            rows = (await db.execute(select(ChannelBinding).where(ChannelBinding.status != "disabled"))).scalars().all()
            targets = [(r.user_id, r.channel) for r in rows]
        for user_id, channel in targets:
            try:
                await self.restart_binding(user_id, channel)
            except Exception:
                logger.exception("failed to start channel binding at boot", extra={"user_id": user_id, "channel": channel})

    async def start_binding(self, user_id: int, channel: str) -> None:
        key = (user_id, channel)
        existing = self._tasks.get(key)
        if existing is not None and not existing.done():
            return
        snapshot = await self._snapshot(user_id, channel)
        if snapshot is None:
            return
        self._tasks[key] = asyncio.create_task(self._run_guarded(snapshot), name=f"channels.binding.{channel}.{user_id}")

    async def stop_binding(self, user_id: int, channel: str) -> None:
        key = (user_id, channel)
        task = self._tasks.pop(key, None)
        self._adapters.pop(key, None)
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def restart_binding(self, user_id: int, channel: str) -> None:
        await self.stop_binding(user_id, channel)
        await self.start_binding(user_id, channel)

    async def drain(self) -> None:
        """lifespan 关闭段：取消并等待全部绑定任务，避免持有连接池的协程逃过 shutdown。"""
        keys = list(self._tasks)
        tasks = [self._tasks.pop(k) for k in keys]
        self._adapters.clear()
        for task in tasks:
            if not task.done():
                task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("channel binding task exited with error during drain")

    async def _snapshot(self, user_id: int, channel: str) -> ChannelBindingSnapshot | None:
        async with session_scope() as db:
            row = (await db.execute(select(ChannelBinding).where(ChannelBinding.user_id == user_id, ChannelBinding.channel == channel))).scalar_one_or_none()
            if row is None:
                return None
            return ChannelBindingSnapshot(
                id=row.id,
                user_id=row.user_id,
                channel=row.channel,
                config=ChannelAdapter.parse_config(row.config_json),
                credentials=row.credentials,
            )

    async def _set_status(self, snapshot: ChannelBindingSnapshot, status: str, *, error: str | None = None) -> None:
        async with session_scope() as db:
            row = await db.get(ChannelBinding, snapshot.id)
            if row is None:
                return
            changed = row.status != status
            row.status = status
            row.last_error = error if status == "error" else None
            if changed:
                db.add(
                    WSEvent(
                        user_id=snapshot.user_id,
                        event_type="channel.status",
                        payload=json.dumps(
                            {
                                "channel": snapshot.channel,
                                "status": status,
                                **({"account_name": row.account_name} if row.account_name else {}),
                                **({"error": error} if error else {}),
                            },
                            ensure_ascii=False,
                        ),
                    ),
                )
            await db.commit()

    async def _run_guarded(self, snapshot: ChannelBindingSnapshot) -> None:
        """守卫循环：fatal ChannelError → 标 error 停止；其他异常/意外返回 → 退避后重建适配器重试。"""
        key = (snapshot.user_id, snapshot.channel)
        while True:
            try:
                adapter = resolve(snapshot.channel)(snapshot, self._make_on_inbound(snapshot))
                self._adapters[key] = adapter
                await self._set_status(snapshot, "connected")
                await adapter.run()
                logger.warning("channel adapter run() returned unexpectedly; restarting", extra={"key": key})
            except asyncio.CancelledError:
                raise
            except ChannelError as e:
                if e.fatal:
                    logger.error("channel binding fatal error", extra={"key": key, "error": str(e)})
                    await self._set_status(snapshot, "error", error=str(e))
                    self._adapters.pop(key, None)
                    return
                logger.warning("channel adapter transient error; backing off", extra={"key": key, "error": str(e)})
            except Exception:
                logger.exception("channel adapter crashed; backing off", extra={"key": key})
            await asyncio.sleep(SETTINGS.channels_restart_backoff_seconds)
            # 重启前刷新快照（凭据/配置可能已被 REST 更新）。
            fresh = await self._snapshot(snapshot.user_id, snapshot.channel)
            if fresh is None:
                # 绑定行已删除（DELETE 竞速）：静默退出，不必标状态。
                self._adapters.pop(key, None)
                return
            snapshot = fresh

    def _make_on_inbound(self, snapshot: ChannelBindingSnapshot) -> OnInbound:
        """入站回调经当前适配器实例转发：守卫循环重建适配器后旧闭包仍指向最新实例。"""

        async def on_inbound(msg: InboundMessage) -> asyncio.Future[str | None]:
            adapter = self.adapter(snapshot.user_id, snapshot.channel)
            if adapter is None:
                fut: asyncio.Future[str | None] = asyncio.get_running_loop().create_future()
                fut.set_result(None)
                return fut
            return await handle_inbound(adapter, msg)

        return on_inbound


MANAGER = ChannelManager()


async def start_channel_manager() -> None:
    await MANAGER.load_and_start()


async def stop_channel_manager() -> None:
    await MANAGER.drain()
