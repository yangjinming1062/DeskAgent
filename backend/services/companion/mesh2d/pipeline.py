"""2D 拆分编排器：see-through 双 provider（HF 主用/魔搭备用）拆分 + 行状态机 + 落库 + WS 事件。"""

import asyncio
import base64
import hashlib
import json

from components import SESSION_LOCAL, get_logger
from modules.companion import Companion2DModel, CompanionOutfit
from modules.ws import emit_ws_event
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .. import asset_store
from ..avatar_service import get_avatar_job_lock, load_avatar_bytes_as_data_uri, normalize_avatar_url_to_bare
from ..room_backdrop_service import invalidate_room_for_outfit
from ..seethrough import SeeThroughError, run_seethrough_split
from .priority_queue import get_default_queue

logger = get_logger(__name__)


class Mesh2DPipelineError(RuntimeError):
    """2d 流水线失败 — 调用方应继续走程序化蛋兜底。"""


# 后台提交任务的强引用集合 + 在飞 model_id 集合（后者供 service 侧识别重启遗留的僵尸 generating 行）
_PIPELINE_TASKS: set[asyncio.Task[None]] = set()
_ACTIVE_MODEL_IDS: set[int] = set()


def active_model_ids() -> frozenset[int]:
    return frozenset(_ACTIVE_MODEL_IDS)


def _safe_load_avatar_bytes(url: str) -> bytes:
    data_uri = load_avatar_bytes_as_data_uri(url)

    if not data_uri or not data_uri.startswith("data:"):
        raise Mesh2DPipelineError(f"failed to read avatar bytes for url: {url}")

    _, b64 = data_uri.split(",", 1)
    return base64.b64decode(b64)


async def _generate(
    user_id: int,
    fullbody_bytes: bytes,
) -> tuple[str, list[dict[str, str]]]:
    """see-through 唯一拆分路径；失败直接报错落失败态，无 CPU 兜底链。"""
    try:
        return await run_seethrough_split(user_id, fullbody_bytes)
    except SeeThroughError as exc:
        raise Mesh2DPipelineError(f"see-through split failed: {exc}") from exc


def run_mesh2d_pipeline(
    *,
    user_id: int,
    model_id: int,
    fullbody_url: str,
    priority: str = "high",
) -> asyncio.Task[None]:
    """提交异步流水线并立即返回；状态写入 Companion2DModel 表，由 WS 事件驱动前端刷新。

    队列 worker 内各阶段自开短会话——请求路径毫秒级返回（202 语义），不与
    视觉 LLM 往返共享请求会话。"""
    queue = get_default_queue()

    async def _fetch_model(db: AsyncSession) -> Companion2DModel | None:
        return (
            await db.execute(
                select(Companion2DModel).where(
                    Companion2DModel.id == model_id,
                    Companion2DModel.user_id == user_id,
                ),
            )
        ).scalar_one_or_none()

    async def _task() -> None:
        async with SESSION_LOCAL() as db:
            model = await _fetch_model(db)

            if model is None:
                logger.warning(
                    "2d model row vanished before pipeline run",
                    extra={"model_id": model_id, "user_id": user_id},
                )
                return

            model.status = "generating"
            model.error = None
            await db.commit()

        try:
            normalized_url = normalize_avatar_url_to_bare(fullbody_url) or fullbody_url
            fullbody_bytes = _safe_load_avatar_bytes(normalized_url)
            manifest_json, layer_entries = await _generate(user_id, fullbody_bytes)
        except Mesh2DPipelineError as exc:
            logger.warning(
                "2d pipeline failed",
                extra={"user_id": user_id, "model_id": model_id, "error": str(exc)},
            )
            await _mark_failed(user_id=user_id, model_id=model_id, error=str(exc), reason=str(exc))
            return
        except Exception as exc:
            logger.exception(
                "2d pipeline crashed",
                extra={"user_id": user_id, "model_id": model_id},
            )
            error = f"unexpected: {exc!s}"
            await _mark_failed(user_id=user_id, model_id=model_id, error=error, reason=error)
            return

        async with SESSION_LOCAL() as db:
            model = await _fetch_model(db)
            if model is None:
                logger.warning("2d model row vanished after pipeline run", extra={"model_id": model_id, "user_id": user_id})
                return
            model.status = "succeeded"
            model.manifest_json = manifest_json
            model.content_hash = hashlib.sha256(manifest_json.encode("utf-8")).hexdigest()
            model.layers_json = json.dumps(layer_entries, ensure_ascii=False)

            manifest_path = asset_store.save_companion_asset(
                manifest_json.encode("utf-8"),
                user_id=user_id,
                label=f"2d_manifest_{model_id}",
                ext="json",
            )
            model.manifest_path = manifest_path
            # outfit 成功接缝：置 ready；自动穿着标记仍真则原子翻转穿着（先停用后激活——
            # 部分唯一索引不可延迟）。标记已被手动穿着清掉时只入柜不换装。
            event_outfit_id = model.outfit_id
            worn: bool | None = None
            event_outfit_name = ""
            if event_outfit_id is not None:
                # 锁内读取标记并翻转——与手动穿着路径互斥，否则「读到标记为真 → 用户手选
                # 提交 → 切分完成覆盖手选」的竞态会击穿两段式激活的用户选择保证
                async with get_avatar_job_lock(user_id):
                    outfit = (
                        await db.execute(
                            select(CompanionOutfit).where(
                                CompanionOutfit.id == event_outfit_id,
                                CompanionOutfit.user_id == user_id,
                            ),
                        )
                    ).scalar_one_or_none()
                    if outfit is not None:
                        outfit.status = "ready"
                        event_outfit_name = outfit.name
                        if outfit.pending_wear:
                            await db.execute(
                                update(Companion2DModel)
                                .where(Companion2DModel.user_id == user_id, Companion2DModel.active.is_(True), Companion2DModel.id != model_id)
                                .values(active=False)
                                .execution_options(synchronize_session=False),
                            )
                            await db.execute(
                                update(CompanionOutfit)
                                .where(CompanionOutfit.user_id == user_id, CompanionOutfit.active.is_(True))
                                .values(active=False)
                                .execution_options(synchronize_session=False),
                            )
                            model.active = True
                            outfit.active = True
                            outfit.pending_wear = False
                            worn = True
                        else:
                            model.active = False
                            worn = False
                    else:
                        # outfit 行已被删除的防御分支：不可激活（现有穿着行仍激活），留孤儿行
                        model.active = False
            else:
                # service 插入时的停用与切分完成之间隔着最长 29 分钟窗口，期间 outfit 可能
                # 已穿着——先停用其余激活行再激活，否则两条 active 并存令激活查询抛错
                await db.execute(
                    update(Companion2DModel)
                    .where(Companion2DModel.user_id == user_id, Companion2DModel.active.is_(True), Companion2DModel.id != model_id)
                    .values(active=False)
                    .execution_options(synchronize_session=False),
                )
                model.active = True
            await db.commit()

        manifest_url = asset_store.signed_companion_asset_url(manifest_path)
        await _emit_mesh2d_ready(user_id, model_id, manifest_url, layer_entries)
        if worn is not None:
            payload: dict = {"outfit_id": event_outfit_id, "worn": worn}
            if event_outfit_name:
                payload["name"] = event_outfit_name
            await _emit_outfit_event(user_id, "companion.outfit.updated", payload)
            if worn:
                try:
                    await invalidate_room_for_outfit(user_id, new_fingerprint=str(event_outfit_id))
                except Exception:
                    logger.warning("mesh2d wear -> room invalidation failed", extra={"user_id": user_id, "outfit_id": event_outfit_id}, exc_info=True)

    async def _submit() -> None:
        _ACTIVE_MODEL_IDS.add(model_id)
        try:
            await queue.submit(f"2d:{user_id}", _task, priority=priority)
        finally:
            _ACTIVE_MODEL_IDS.discard(model_id)

    wrapper = asyncio.create_task(_submit())

    def _done(task: asyncio.Task[None]) -> None:
        _PIPELINE_TASKS.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.error("2d pipeline submission failed", exc_info=task.exception())

    _PIPELINE_TASKS.add(wrapper)
    wrapper.add_done_callback(_done)
    return wrapper


async def _mark_failed(*, user_id: int, model_id: int, error: str, reason: str) -> None:
    """失败态落库 + 事件下发；自身异常只记日志（失败路径不能再抛）。"""
    try:
        outfit_failed_id: int | None = None
        async with SESSION_LOCAL() as db:
            model = (
                await db.execute(
                    select(Companion2DModel).where(
                        Companion2DModel.id == model_id,
                        Companion2DModel.user_id == user_id,
                    ),
                )
            ).scalar_one_or_none()
            if model is not None:
                model.status = "failed"
                model.error = error
                if model.outfit_id is not None:
                    outfit = (
                        await db.execute(
                            select(CompanionOutfit).where(
                                CompanionOutfit.id == model.outfit_id,
                                CompanionOutfit.user_id == user_id,
                            ),
                        )
                    ).scalar_one_or_none()
                    if outfit is not None and outfit.status == "splitting":
                        outfit.status = "failed"
                        outfit.pending_wear = False
                        outfit_failed_id = outfit.id
                await db.commit()
        await _emit_mesh2d_failed(user_id, model_id, reason=reason)
        if outfit_failed_id is not None:
            await _emit_outfit_event(user_id, "companion.outfit.failed", {"outfit_id": outfit_failed_id, "reason": reason})
    except Exception:
        logger.warning("2d failed-state persistence error", exc_info=True)


async def _emit_outfit_event(user_id: int, event_type: str, payload: dict) -> None:
    try:
        async with SESSION_LOCAL() as db:
            emit_ws_event(db, user_id=user_id, event_type=event_type, payload=payload)
            await db.commit()
    except Exception:
        logger.warning("Failed to emit %s", event_type, exc_info=True)


async def _emit_mesh2d_ready(
    user_id: int,
    model_id: int,
    manifest_url: str | None,
    layer_entries: list[dict[str, str]],
) -> None:
    """通过 ws_events 表写入一条 WS 事件；事件推送由 chat ws 通道消费。"""
    try:
        payload = {
            "model_id": model_id,
            "manifest_url": manifest_url,
            "layers": layer_entries,
        }
        async with SESSION_LOCAL() as db:
            emit_ws_event(db, user_id=user_id, event_type="companion.2d.ready", payload=payload)
            await db.commit()
    except Exception:
        logger.warning("Failed to emit companion.2d.ready", exc_info=True)


async def _emit_mesh2d_failed(
    user_id: int,
    model_id: int,
    *,
    reason: str,
) -> None:
    try:
        payload = {"model_id": model_id, "reason": reason}
        async with SESSION_LOCAL() as db:
            emit_ws_event(db, user_id=user_id, event_type="companion.2d.failed", payload=payload)
            await db.commit()
    except Exception:
        logger.warning("Failed to emit companion.2d.failed", exc_info=True)
