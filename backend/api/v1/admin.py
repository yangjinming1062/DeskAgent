import json

from common import get_or_404
from common import get_router
from common import list_response
from components import apply_partial
from components import get_db
from fastapi import Depends
from fastapi import HTTPException
from fastapi import status
from modules.auth import fingerprint_api_key
from modules.auth import get_current_admin_token
from modules.auth import hash_password
from modules.auth import public_provider_slots
from modules.auth import User
from modules.auth import UserCreate
from modules.auth import UserListResponse
from modules.auth import UserModelConfig
from modules.auth import UserModelConfigListItem
from modules.auth import UserModelConfigListResponse
from modules.auth import UserModelConfigRequest
from modules.auth import UserResponse
from modules.auth import UserUpdate
from sqlalchemy.orm import Session

router = get_router()


@router.get("/users", response_model=UserListResponse)
def list_users(_admin: str = Depends(get_current_admin_token), db: Session = Depends(get_db)) -> UserListResponse:
    return list_response(db.query(User).order_by(User.id).all(), UserResponse, UserListResponse)


@router.get("/users/{user_id}", response_model=UserResponse)
def get_user(user_id: int, _admin: str = Depends(get_current_admin_token), db: Session = Depends(get_db)) -> UserResponse:
    return UserResponse.model_validate(get_or_404(db, User, id=user_id, detail="用户不存在。"))


@router.post("/users", response_model=UserResponse)
def create_user(payload: UserCreate, _admin: str = Depends(get_current_admin_token), db: Session = Depends(get_db)) -> UserResponse:
    if db.query(User).filter(User.username == payload.username).one_or_none():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="用户名已存在。")
    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        can_use=payload.can_use,
        expires_at=payload.expires_at,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return UserResponse.model_validate(user)


@router.patch("/users/{user_id}", response_model=UserResponse)
def update_user(user_id: int, payload: UserUpdate, _admin: str = Depends(get_current_admin_token), db: Session = Depends(get_db)) -> UserResponse:
    user = get_or_404(db, User, id=user_id, detail="用户不存在。")
    if payload.password is not None:
        user.password_hash = hash_password(payload.password)
    # 显式 null 清空:不放入 apply_partial.exclude,否则正常的"设置未来过期时间"也会被跳过
    if "expires_at" in payload.model_fields_set and payload.expires_at is None:
        user.expires_at = None
    apply_partial(user, payload, exclude={"password"})
    db.commit()
    return UserResponse.model_validate(user)


@router.delete("/users/{user_id}", response_model=dict)
def delete_user(user_id: int, _admin: str = Depends(get_current_admin_token), db: Session = Depends(get_db)) -> dict:
    db.delete(get_or_404(db, User, id=user_id, detail="用户不存在。"))
    db.commit()
    return {"message": "用户已删除。"}


@router.patch("/users/{user_id}/toggle-active")
def toggle_user_active(user_id: int, _admin: str = Depends(get_current_admin_token), db: Session = Depends(get_db)) -> UserResponse:
    user = get_or_404(db, User, id=user_id, detail="用户不存在。")
    user.is_active = not user.is_active
    db.commit()
    return UserResponse.model_validate(user)


def _config_list_item(r: UserModelConfig) -> UserModelConfigListItem:
    return UserModelConfigListItem(
        user_id=r.user_id,
        llm_base_url=r.llm_base_url,
        llm_api_key_fingerprint=fingerprint_api_key(r.llm_api_key),
        llm_api_key_set=bool(r.llm_api_key),
        llm_model_name=r.llm_model_name,
        stt_base_url=r.stt_base_url,
        stt_api_key_set=bool(r.stt_api_key),
        stt_model_name=r.stt_model_name,
        tts_base_url=r.tts_base_url,
        tts_api_key_set=bool(r.tts_api_key),
        tts_model_name=r.tts_model_name,
        image_gen_base_url=r.image_gen_base_url,
        image_gen_api_key_set=bool(r.image_gen_api_key),
        image_gen_model_name=r.image_gen_model_name,
        video_gen_base_url=r.video_gen_base_url,
        video_gen_api_key_set=bool(r.video_gen_api_key),
        video_gen_model_name=r.video_gen_model_name,
        provider_config=public_provider_slots(r.provider_config),
    )


def _merged_provider_json(payload: UserModelConfigRequest, existing: UserModelConfig | None) -> str:
    # An empty api_key keeps the existing key for that provider — the admin
    # can't see the raw value, so "leave blank" must mean "no change".
    prev = {s["name"]: s.get("api_key", "") for s in json.loads(existing.provider_config or "[]")} if existing else {}
    out = []
    for slot in payload.provider_config:
        d = slot.model_dump()
        if not d.get("api_key") and d["name"] in prev:
            d["api_key"] = prev[d["name"]]
        out.append(d)
    return json.dumps(out)


@router.get("/model-configs", response_model=UserModelConfigListResponse)
def list_model_configs(_admin: str = Depends(get_current_admin_token), db: Session = Depends(get_db)) -> UserModelConfigListResponse:
    return UserModelConfigListResponse(items=[_config_list_item(r) for r in db.query(UserModelConfig).all()])


@router.put("/{user_id}/model-config")
def upsert_model_config(user_id: int, payload: UserModelConfigRequest, _admin: str = Depends(get_current_admin_token), db: Session = Depends(get_db)) -> dict:
    get_or_404(db, User, id=user_id, detail="用户不存在。")
    config = db.query(UserModelConfig).filter(UserModelConfig.user_id == user_id).one_or_none()
    provider_json = _merged_provider_json(payload, config)
    if config:
        apply_partial(config, payload, exclude=frozenset({"provider_config"}))
        config.provider_config = provider_json
    else:
        data = payload.model_dump()
        data["provider_config"] = provider_json
        db.add(UserModelConfig(user_id=user_id, **data))
    db.commit()
    return {"message": "模型配置已更新。"}


@router.delete("/{user_id}/model-config")
def delete_model_config(user_id: int, _admin: str = Depends(get_current_admin_token), db: Session = Depends(get_db)) -> dict:
    db.delete(get_or_404(db, UserModelConfig, user_id=user_id, detail="模型配置不存在。"))
    db.commit()
    return {"message": "模型配置已删除。"}
