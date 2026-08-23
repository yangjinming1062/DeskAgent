"""Mesh2D service — 业务逻辑层：在 finalize 后触发切分、查询活跃模型、切换渲染模式。"""

import json

from components import get_logger
from modules.companion import AvatarAsset, Mesh2DModel, Persona
from modules.companion.schemas import Mesh2DModelResponse
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .. import asset_store
from ..persona_service import get_or_create_persona
from .pipeline import run_mesh2d_pipeline

logger = get_logger(__name__)


class Mesh2DAlreadyRunningError(RuntimeError):
    """已有 mesh2d 切分任务在进行中，或 avatar 尚未就绪无法启动切分。"""


async def _resolve_active_avatar(db: AsyncSession, user_id: int) -> AvatarAsset | None:
    return (await db.execute(select(AvatarAsset).where(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)))).scalar_one_or_none()


async def generate_mesh2d_model(
    db: AsyncSession,
    *,
    user_id: int,
    priority: str = "high",
    force: bool = False,
) -> Mesh2DModel:
    """从已激活 avatar 启动 mesh2d 切分；avatar 不可用时抛错。"""
    avatar = await _resolve_active_avatar(db, user_id)

    if avatar is None or not (avatar.seed_front_url or avatar.asset_url):
        raise Mesh2DAlreadyRunningError("请先完成形象生成后再启动 2D 切分")

    fullbody_url = avatar.seed_front_url or avatar.asset_url

    if not force:
        existing = (await db.execute(select(Mesh2DModel).where(Mesh2DModel.user_id == user_id, Mesh2DModel.status == "generating"))).scalar_one_or_none()

        if existing is not None:
            logger.info("mesh2d generation already in flight", extra={"user_id": user_id, "model_id": existing.id})
            return existing

        active = (
            await db.execute(
                select(Mesh2DModel).where(
                    Mesh2DModel.user_id == user_id,
                    Mesh2DModel.active.is_(True),
                    Mesh2DModel.status == "succeeded",
                ),
            )
        ).scalar_one_or_none()

        if active is not None:
            logger.info("active mesh2d already exists", extra={"user_id": user_id, "model_id": active.id})
            return active

    await db.execute(
        update(Mesh2DModel).where(Mesh2DModel.user_id == user_id, Mesh2DModel.active.is_(True), Mesh2DModel.status.in_(("generating", "succeeded"))).values(active=False),
    )

    model = Mesh2DModel(user_id=user_id, avatar_id=avatar.id, status="generating", priority=priority)
    db.add(model)
    await db.commit()
    await db.refresh(model)

    await run_mesh2d_pipeline(db, user_id=user_id, model_id=model.id, fullbody_url=fullbody_url, priority=priority)
    logger.info("mesh2d pipeline kicked off", extra={"user_id": user_id, "model_id": model.id, "priority": priority})
    return model


async def get_active_mesh2d_response(db: AsyncSession, user_id: int) -> Mesh2DModelResponse | None:
    """把活跃 mesh2d 模型转换为 API 响应；客户端拿到 manifest_url 后启动 SkinnedMesh 渲染。"""
    model = (
        await db.execute(
            select(Mesh2DModel).where(
                Mesh2DModel.user_id == user_id,
                Mesh2DModel.active.is_(True),
                Mesh2DModel.status == "succeeded",
            ),
        )
    ).scalar_one_or_none()

    if model is None:
        return None

    manifest_url = asset_store.signed_companion_asset_url(model.manifest_path) if model.manifest_path else None

    layer_urls: dict[str, str] = {}

    try:
        raw_layers = json.loads(model.layers_json or "[]")

        if isinstance(raw_layers, list):
            for entry in raw_layers:
                if isinstance(entry, dict) and entry.get("name") and entry.get("url"):
                    signed = asset_store.signed_companion_asset_url(entry["url"])
                    if signed:
                        layer_urls[entry["name"]] = signed
    except Exception as exc:
        logger.warning("failed to parse mesh2d layers_json", extra={"error": str(exc)})

    return Mesh2DModelResponse(
        id=model.id,
        status=model.status,
        style=model.style or "cel_shading",
        manifest_url=manifest_url,
        layer_urls=layer_urls,
        content_hash=model.content_hash,
        error=model.error,
    )


async def set_render_mode(db: AsyncSession, *, user_id: int, render_mode: str) -> Persona:
    """写入 render_mode；切到 3D 时由调用方触发 3D 流水线（model_service）。"""
    persona = await get_or_create_persona(db, user_id)
    persona.render_mode = render_mode
    await db.commit()
    await db.refresh(persona)
    logger.info("persona render_mode updated", extra={"user_id": user_id, "render_mode": render_mode})

    return persona


async def reset_mesh2d(db: AsyncSession, user_id: int) -> None:
    """avatar 重新生成时调用：supersede 旧 mesh2d 让客户端回退到程序化蛋。"""
    await db.execute(
        update(Mesh2DModel).where(Mesh2DModel.user_id == user_id, Mesh2DModel.active.is_(True)).values(active=False),
    )
    await db.commit()
    logger.info("mesh2d models superseded for avatar regen", extra={"user_id": user_id})
