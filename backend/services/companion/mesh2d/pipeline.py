"""2D 主流水线：视觉 LLM 区域识别 → CPU 抠图 → 关键点估计 → 骨骼装配 → manifest + 资产落盘。"""

import asyncio
import base64
import hashlib
import json

from components import SESSION_LOCAL, get_logger
from modules.companion import Companion2DModel, CompanionOutfit
from modules.ws.models import WSEvent
from sqlalchemy import select, update

from services.llm import resolve_vision_chain

from .. import asset_store
from ..avatar_service import _normalize_avatar_url_to_bare, get_avatar_job_lock, load_avatar_bytes_as_data_uri
from .layer_extractor import extract_layers, layer_centers
from .llm_validator import validate_layers
from .manifest_exporter import build_manifest
from .occlusion_resolver import fill_occlusion
from .pose_estimator import estimate_pose, sanitize_keypoints
from .priority_queue import get_default_queue
from .region_detector import detect_regions
from .skeleton_builder import build_bones, build_meshes

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


async def _run_pipeline_core(
    user_id: int,
    fullbody_url: str,
    *,
    canvas: tuple[int, int] = (1024, 1366),
) -> tuple[str, list[dict[str, str]]]:
    """执行 4 阶段：识别 → 抠图 → 关键点 → manifest；返回 (manifest_json, [{name, url}, ...])。"""
    fullbody_bytes = _safe_load_avatar_bytes(fullbody_url)
    data_uri = "data:image/png;base64," + base64.b64encode(fullbody_bytes).decode(
        "ascii",
    )

    # 视觉链在短会话内预解析，两段 LLM 调用期间不持有连接（ARCH §短会话纪律）
    async with SESSION_LOCAL() as db:
        chain = await resolve_vision_chain(db, user_id)

    layers = await detect_regions(chain, user_id, data_uri)

    if not layers:
        raise Mesh2DPipelineError("vision LLM region detection returned empty result")

    extracted = extract_layers(fullbody_bytes, layers)

    if not extracted:
        raise Mesh2DPipelineError("CPU matting extracted zero usable layers")

    centers = layer_centers(extracted)
    ok, reason = validate_layers(extracted)

    if not ok:
        raise Mesh2DPipelineError(f"layer validation failed: {reason}")

    extracted = fill_occlusion(extracted)

    kp_raw = await estimate_pose(chain, user_id, data_uri, layer_centers=centers)
    kp = sanitize_keypoints(kp_raw, layer_centers=centers)

    bones = build_bones(kp, extracted, canvas_w=canvas[0], canvas_h=canvas[1])
    meshes = build_meshes(extracted, canvas_w=canvas[0], canvas_h=canvas[1])
    has_legs = any(layer.name in {"leg_L", "leg_R"} for layer in extracted)
    manifest = build_manifest(bones, meshes, canvas, has_legs=has_legs)
    manifest_json = manifest.to_json()

    layer_entries: list[dict[str, str]] = []

    for layer in extracted:
        storage_path = asset_store.save_companion_asset(
            layer.png_bytes,
            user_id=user_id,
            label=f"2d_{layer.name}",
            ext="png",
        )
        layer_entries.append({"name": layer.name, "url": storage_path})

    return manifest_json, layer_entries


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

    async def _fetch_model(db) -> Companion2DModel | None:
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
            normalized_url = _normalize_avatar_url_to_bare(fullbody_url) or fullbody_url
            manifest_json, layer_entries = await _run_pipeline_core(
                user_id,
                normalized_url,
            )
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
            # 部分唯一索引不可延迟）。标记已被手动穿着清掉时只入柜不换装；
            # 非 outfit 行维持原行为（service 插入前已停用旧行，直接激活）。
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
                                .values(active=False),
                                synchronize_session=False,
                            )
                            await db.execute(
                                update(CompanionOutfit).where(CompanionOutfit.user_id == user_id, CompanionOutfit.active.is_(True)).values(active=False),
                                synchronize_session=False,
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
                model.active = True
            await db.commit()

        manifest_url = asset_store.signed_companion_asset_url(manifest_path)
        await _emit_mesh2d_ready(user_id, model_id, manifest_url, layer_entries)
        if worn is not None:
            payload: dict = {"outfit_id": event_outfit_id, "worn": worn}
            if event_outfit_name:
                payload["name"] = event_outfit_name
            await _emit_outfit_event(user_id, "companion.outfit.updated", payload)

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
            db.add(
                WSEvent(
                    user_id=user_id,
                    event_type=event_type,
                    payload=json.dumps(payload, ensure_ascii=False),
                ),
            )
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
            db.add(
                WSEvent(
                    user_id=user_id,
                    event_type="companion.2d.ready",
                    payload=json.dumps(payload, ensure_ascii=False),
                ),
            )
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
            db.add(
                WSEvent(
                    user_id=user_id,
                    event_type="companion.2d.failed",
                    payload=json.dumps(payload, ensure_ascii=False),
                ),
            )
            await db.commit()
    except Exception:
        logger.warning("Failed to emit companion.2d.failed", exc_info=True)
