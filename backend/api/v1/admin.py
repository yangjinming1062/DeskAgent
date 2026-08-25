import contextlib
import shutil
from pathlib import Path

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
    decode_activation_code,
    encode_activation_code,
    fingerprint_api_key,
    generate_activation_token,
    get_current_admin_token,
    hash_activation_token,
    public_provider_slots,
)
from modules.companion import AvatarAsset, Companion3DModel
from modules.system import MessageResponse
from services.companion.avatar_service import _delete_portrait_file
from services.gateway import MANAGER, cancel_user_cron_turns, discard_user
from services.gateway.handlers import _USER_SESSIONS
from services.llm import merge_provider_json
from services.tools import REGISTRY
from sqlalchemy import delete, select
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
    code = encode_activation_code(payload.base_url, raw_token)
    user = User(username=payload.username, activation_code=code, activation_token_hash=hash_activation_token(raw_token), nightly_activity_enabled=payload.nightly_activity_enabled)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return UserResponse.model_validate(user)


@router.patch("/users/{user_id}", response_model=UserResponse)
async def update_user(user_id: int, payload: UserUpdate, _admin: str = Depends(get_current_admin_token), db: AsyncSession = Depends(get_db)) -> UserResponse:
    user = await get_or_404(db, User, id=user_id, detail="用户不存在。")
    if payload.regenerate_token:
        raw_token = generate_activation_token()
        user.activation_token_hash = hash_activation_token(raw_token)
        base_url = payload.base_url
        if not base_url and user.activation_code:
            try:
                base_url = decode_activation_code(user.activation_code)[0]
            except Exception:
                base_url = "http://localhost:10620"
        user.activation_code = encode_activation_code(base_url or "http://localhost:10620", raw_token)
    elif payload.base_url:
        if user.activation_code:
            try:
                _, token = decode_activation_code(user.activation_code)
                user.activation_code = encode_activation_code(payload.base_url, token)
            except Exception:
                pass
    apply_partial(user, payload, exclude={"regenerate_token", "base_url"})
    await db.commit()
    await db.refresh(user)
    return UserResponse.model_validate(user)


@router.delete("/users/{user_id}", response_model=MessageResponse)
async def delete_user(user_id: int, _admin: str = Depends(get_current_admin_token), db: AsyncSession = Depends(get_db)) -> MessageResponse:
    await get_or_404(db, User, id=user_id, detail="用户不存在。")
    # 主动踢掉活动 WS，让被删除用户的 renderer 干净断开。
    ws = MANAGER.active_connections.get(user_id)
    if ws is not None:
        with contextlib.suppress(Exception):
            await ws.close(code=1000)
        MANAGER.disconnect(ws, user_id)
    cancel_user_cron_turns(user_id)
    await MANAGER.aunregister_dispatcher(user_id)
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

    # 清除用户范围内的 DB 行与磁盘资产（被遗忘权）。
    avatar_rows = (await db.execute(select(AvatarAsset).where(AvatarAsset.user_id == user_id))).scalars().all()
    for av in avatar_rows:
        _delete_portrait_file(av.asset_url)

    await db.execute(delete(AvatarAsset).where(AvatarAsset.user_id == user_id))
    await db.execute(delete(Companion3DModel).where(Companion3DModel.user_id == user_id))
    await db.delete(await db.get(User, user_id))
    await db.commit()

    for sub in ("companion-assets", "companion-models"):
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
    # 管理员写入必须三字段齐全；行内字段不全会静默打断用户聊天链路（PROTOCOL §5.4）。
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
