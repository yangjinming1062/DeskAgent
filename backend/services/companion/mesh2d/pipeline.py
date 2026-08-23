"""Mesh2D 主流水线：视觉 LLM 区域识别 → CPU 抠图 → 关键点估计 → 骨骼装配 → manifest + 资产落盘。"""

import base64
import hashlib
import json

from components import get_logger
from modules.companion import Mesh2DModel
from modules.ws.models import WSEvent
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import asset_store
from ..avatar_service import (
    _normalize_avatar_url_to_bare,
    load_avatar_bytes_as_data_uri,
)
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
    """mesh2d 流水线失败 — 调用方应继续走程序化蛋兜底。"""


def _safe_load_avatar_bytes(url: str) -> bytes:
    data_uri = load_avatar_bytes_as_data_uri(url)

    if not data_uri or not data_uri.startswith("data:"):
        raise Mesh2DPipelineError(f"failed to read avatar bytes for url: {url}")

    _, b64 = data_uri.split(",", 1)
    return base64.b64decode(b64)


async def _run_pipeline_core(
    db: AsyncSession | None,
    user_id: int,
    fullbody_url: str,
    *,
    canvas: tuple[int, int] = (1024, 1366),
) -> tuple[str, list[dict[str, str]]]:
    """执行 4 阶段：识别 → 抠图 → 关键点 → manifest；返回 (manifest_json, [{name, url}, ...])。"""
    fullbody_bytes = _safe_load_avatar_bytes(fullbody_url)
    data_uri = "data:image/png;base64," + base64.b64encode(fullbody_bytes).decode("ascii")

    layers = await detect_regions(db, user_id, data_uri)

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

    kp_raw = await estimate_pose(db, user_id, data_uri, layer_centers=centers)
    kp = sanitize_keypoints(kp_raw, layer_centers=centers)

    bones = build_bones(kp, extracted, canvas_w=canvas[0], canvas_h=canvas[1])
    meshes = build_meshes(extracted, canvas_w=canvas[0], canvas_h=canvas[1])
    manifest = build_manifest(bones, meshes, canvas)
    manifest_json = manifest.to_json()

    layer_entries: list[dict[str, str]] = []

    for layer in extracted:
        storage_path = asset_store.save_companion_asset(
            layer.png_bytes,
            user_id=user_id,
            label=f"mesh2d_{layer.name}",
            ext="png",
        )
        layer_entries.append({"name": layer.name, "url": storage_path})

    return manifest_json, layer_entries


async def run_mesh2d_pipeline(
    db: AsyncSession,
    *,
    user_id: int,
    model_id: int,
    fullbody_url: str,
    priority: str = "high",
) -> None:
    """异步执行流水线；状态写入 Mesh2DModel 表，由 WS 事件驱动前端刷新。"""
    queue = get_default_queue()

    async def _task() -> None:
        model = (await db.execute(select(Mesh2DModel).where(Mesh2DModel.id == model_id, Mesh2DModel.user_id == user_id))).scalar_one_or_none()

        if model is None:
            logger.warning("mesh2d model row vanished before pipeline run", extra={"model_id": model_id, "user_id": user_id})
            return

        model.status = "generating"
        model.error = None
        await db.commit()

        try:
            normalized_url = _normalize_avatar_url_to_bare(fullbody_url) or fullbody_url
            manifest_json, layer_entries = await _run_pipeline_core(db, user_id, normalized_url)
        except Mesh2DPipelineError as exc:
            logger.warning("mesh2d pipeline failed", extra={"user_id": user_id, "model_id": model_id, "error": str(exc)})
            await db.refresh(model)
            model.status = "failed"
            model.error = str(exc)
            await db.commit()
            await _emit_mesh2d_failed(db, user_id, model_id, reason=str(exc))
            return
        except Exception as exc:
            logger.exception("mesh2d pipeline crashed", extra={"user_id": user_id, "model_id": model_id})
            await db.refresh(model)
            model.status = "failed"
            model.error = f"unexpected: {exc!s}"
            await db.commit()
            await _emit_mesh2d_failed(db, user_id, model_id, reason=model.error or "unknown")
            return

        await db.refresh(model)
        model.status = "succeeded"
        model.manifest_json = manifest_json
        model.content_hash = hashlib.sha256(manifest_json.encode("utf-8")).hexdigest()
        model.layers_json = json.dumps(layer_entries, ensure_ascii=False)

        manifest_path = asset_store.save_companion_asset(
            manifest_json.encode("utf-8"),
            user_id=user_id,
            label=f"mesh2d_manifest_{model_id}",
            ext="json",
        )
        model.manifest_path = manifest_path
        model.active = True
        await db.commit()

        manifest_url = asset_store.signed_companion_asset_url(manifest_path)
        await _emit_mesh2d_ready(db, user_id, model_id, manifest_url, layer_entries)

    await queue.submit(f"mesh2d:{user_id}", _task, priority=priority)


async def _emit_mesh2d_ready(db: AsyncSession, user_id: int, model_id: int, manifest_url: str | None, layer_entries: list[dict[str, str]]) -> None:
    """通过 ws_events 表写入一条 WS 事件；事件推送由 chat ws 通道消费。"""
    try:
        payload = {"model_id": model_id, "manifest_url": manifest_url, "layers": layer_entries}
        db.add(WSEvent(user_id=user_id, event_type="companion.mesh2d.ready", payload=json.dumps(payload, ensure_ascii=False)))
        await db.commit()
    except Exception:
        logger.warning("Failed to emit companion.mesh2d.ready", exc_info=True)


async def _emit_mesh2d_failed(db: AsyncSession, user_id: int, model_id: int, *, reason: str) -> None:
    try:
        payload = {"model_id": model_id, "reason": reason}
        db.add(WSEvent(user_id=user_id, event_type="companion.mesh2d.failed", payload=json.dumps(payload, ensure_ascii=False)))
        await db.commit()
    except Exception:
        logger.warning("Failed to emit companion.mesh2d.failed", exc_info=True)
