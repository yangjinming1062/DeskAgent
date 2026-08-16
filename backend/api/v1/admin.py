from common import get_or_404, get_router, list_response
from components import SETTINGS, apply_partial, get_db
from fastapi import Depends, HTTPException, status
from modules.auth import (
    User,
    UserCreate,
    UserListResponse,
    UserModelConfig,
    UserModelConfigListItem,
    UserModelConfigListResponse,
    UserModelConfigRequest,
    UserResponse,
    UserUpdate,
    encode_activation_code,
    fingerprint_api_key,
    generate_activation_token,
    get_current_admin_token,
    hash_activation_token,
    public_provider_slots,
)
from modules.system import MessageResponse
from services.llm import merge_provider_json
from sqlalchemy import delete as sa_delete, select
from sqlalchemy.ext.asyncio import AsyncSession

router = get_router()


@router.get("/users", response_model=UserListResponse)
async def list_users(_admin: str = Depends(get_current_admin_token), db: AsyncSession = Depends(get_db)) -> UserListResponse:
    return list_response((await db.execute(select(User).order_by(User.id))).scalars().all(), UserResponse, UserListResponse)


@router.get("/users/{user_id}", response_model=UserResponse)
async def get_user(user_id: int, _admin: str = Depends(get_current_admin_token), db: AsyncSession = Depends(get_db)) -> UserResponse:
    return UserResponse.model_validate(await get_or_404(db, User, id=user_id, detail="用户不存在。"))


@router.post("/users", response_model=UserResponse)
async def create_user(payload: UserCreate, _admin: str = Depends(get_current_admin_token), db: AsyncSession = Depends(get_db)) -> UserResponse:
    if (await db.execute(select(User).where(User.username == payload.username))).scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="用户名已存在。")
    raw_token = generate_activation_token()
    user = User(username=payload.username, password_hash=None, activation_token_hash=hash_activation_token(raw_token), can_use=payload.can_use, expires_at=payload.expires_at)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    resp = UserResponse.model_validate(user)
    resp.activation_code = encode_activation_code(payload.base_url, raw_token)
    return resp


@router.patch("/users/{user_id}", response_model=UserResponse)
async def update_user(user_id: int, payload: UserUpdate, _admin: str = Depends(get_current_admin_token), db: AsyncSession = Depends(get_db)) -> UserResponse:
    user = await get_or_404(db, User, id=user_id, detail="用户不存在。")
    raw_token: str | None = None
    if payload.regenerate_token:
        raw_token = generate_activation_token()
        user.activation_token_hash = hash_activation_token(raw_token)
    # 显式 null 清空:不放入 apply_partial.exclude,否则正常的"设置未来过期时间"也会被跳过
    if "expires_at" in payload.model_fields_set and payload.expires_at is None:
        user.expires_at = None
    apply_partial(user, payload, exclude={"regenerate_token", "base_url"})
    await db.commit()
    resp = UserResponse.model_validate(user)
    if raw_token is not None:
        base_url = payload.base_url or "http://localhost:10620"
        resp.activation_code = encode_activation_code(base_url, raw_token)
    return resp


@router.delete("/users/{user_id}", response_model=MessageResponse)
async def delete_user(user_id: int, _admin: str = Depends(get_current_admin_token), db: AsyncSession = Depends(get_db)) -> MessageResponse:
    await get_or_404(db, User, id=user_id, detail="用户不存在。")
    # Kick active WS so a deleted user's renderer gets disconnected cleanly.
    from services.gateway.connection import cancel_user_cron_turns
    from services.gateway.handlers import (
        REGISTRY,
        MANAGER as _MANAGER,
        _USER_SESSIONS,
        discard_user,
    )
    import contextlib
    from services.companion.asset_store import unlink_companion_asset

    ws = _MANAGER.active_connections.get(user_id)
    if ws is not None:
        with contextlib.suppress(Exception):
            await ws.close(code=1000)
        _MANAGER.disconnect(ws, user_id)
    cancel_user_cron_turns(user_id)
    _MANAGER.unregister_dispatcher(user_id)
    REGISTRY.clear_runner_tools(user_id)
    sess = _USER_SESSIONS.pop(user_id, None)
    if sess is not None:
        if sess.grace_timer_task and not sess.grace_timer_task.done():
            sess.grace_timer_task.cancel()
        for t in list(sess.background_tasks):
            if not t.done():
                t.cancel()
        sess.runtime_sessions.clear()
    discard_user(user_id)

    # Wipe user-scoped DB rows + on-disk assets (right-to-be-forgotten).
    from sqlalchemy import delete as sa_delete

    from modules.companion import AvatarAsset, CompanionModel, WardrobeItem

    await db.execute(sa_delete(AvatarAsset).where(AvatarAsset.user_id == user_id))
    await db.execute(sa_delete(CompanionModel).where(CompanionModel.user_id == user_id))
    await db.execute(sa_delete(WardrobeItem).where(WardrobeItem.user_id == user_id))
    await db.delete(await db.get(User, user_id))
    await db.commit()

    import shutil

    for sub in ("companion-assets", "companion-avatars", "companion-models"):
        d = Path(SETTINGS.data_dir) / sub / str(user_id)
        if d.exists():
            with contextlib.suppress(Exception):
                if d.is_dir():
                    shutil.rmtree(d, ignore_errors=True)
                else:
                    d.unlink(missing_ok=True)
    return {"message": "用户已删除。"}


@router.patch("/users/{user_id}/toggle-active")
async def toggle_user_active(user_id: int, _admin: str = Depends(get_current_admin_token), db: AsyncSession = Depends(get_db)) -> UserResponse:
    user = await get_or_404(db, User, id=user_id, detail="用户不存在。")
    user.is_active = not user.is_active
    await db.commit()
    return UserResponse.model_validate(user)


def _config_list_item(r: UserModelConfig) -> UserModelConfigListItem:
    return UserModelConfigListItem(
        user_id=r.user_id,
        llm_provider=r.llm_provider or "",
        llm_base_url=r.llm_base_url,
        llm_api_key_fingerprint=fingerprint_api_key(r.llm_api_key),
        llm_api_key_set=bool(r.llm_api_key),
        llm_model_name=r.llm_model_name,
        stt_provider=r.stt_provider or "",
        stt_base_url=r.stt_base_url,
        stt_api_key_set=bool(r.stt_api_key),
        stt_model_name=r.stt_model_name,
        tts_provider=r.tts_provider or "",
        tts_base_url=r.tts_base_url,
        tts_api_key_set=bool(r.tts_api_key),
        tts_model_name=r.tts_model_name,
        image_gen_provider=r.image_gen_provider or "",
        image_gen_base_url=r.image_gen_base_url,
        image_gen_api_key_set=bool(r.image_gen_api_key),
        image_gen_model_name=r.image_gen_model_name,
        video_gen_provider=r.video_gen_provider or "",
        video_gen_base_url=r.video_gen_base_url,
        video_gen_api_key_set=bool(r.video_gen_api_key),
        video_gen_model_name=r.video_gen_model_name,
        provider_config=public_provider_slots(r.provider_config),
    )


@router.get("/model-configs", response_model=UserModelConfigListResponse)
async def list_model_configs(_admin: str = Depends(get_current_admin_token), db: AsyncSession = Depends(get_db)) -> UserModelConfigListResponse:
    return UserModelConfigListResponse(items=[_config_list_item(r) for r in (await db.execute(select(UserModelConfig))).scalars().all()])


@router.put("/{user_id}/model-config")
async def upsert_model_config(user_id: int, payload: UserModelConfigRequest, _admin: str = Depends(get_current_admin_token), db: AsyncSession = Depends(get_db)) -> MessageResponse:
    # Unlike the self-service endpoint (empty = keep/clear), an admin write
    # must be complete — a partially-filled row would silently break the
    # user's chat chain (PROTOCOL §5.4).
    if not (payload.llm_base_url and payload.llm_api_key and payload.llm_model_name):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="base_url、api_key、model_name 三字段必填。")
    await get_or_404(db, User, id=user_id, detail="用户不存在。")
    config = (await db.execute(select(UserModelConfig).where(UserModelConfig.user_id == user_id))).scalar_one_or_none()
    provider_json = merge_provider_json(payload.provider_config, config)
    if config:
        apply_partial(config, payload, exclude=frozenset({"provider_config"}))
        config.provider_config = provider_json
    else:
        data = payload.model_dump()
        data["provider_config"] = provider_json
        db.add(UserModelConfig(user_id=user_id, **data))
    await db.commit()
    return {"message": "模型配置已更新。"}


@router.delete("/{user_id}/model-config")
async def delete_model_config(user_id: int, _admin: str = Depends(get_current_admin_token), db: AsyncSession = Depends(get_db)) -> MessageResponse:
    await db.delete(await get_or_404(db, UserModelConfig, user_id=user_id, detail="模型配置不存在。"))
    await db.commit()
    return {"message": "模型配置已删除。"}
