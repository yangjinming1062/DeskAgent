from common import get_or_404
from common import get_router
from common import list_response
from components import apply_partial
from components import get_db
from fastapi import Depends
from fastapi import HTTPException
from fastapi import status
from modules.auth import encode_activation_code
from modules.auth import fingerprint_api_key
from modules.auth import generate_activation_token
from modules.auth import get_current_admin_token
from modules.auth import hash_activation_token
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
from services.llm import merge_provider_json
from sqlalchemy.orm import Session

router = get_router()


@router.get("/users", response_model=UserListResponse)
def list_users(_admin: str = Depends(get_current_admin_token), db: Session = Depends(get_db)) -> UserListResponse:
    return list_response(db.query(User).order_by(User.id).all(), UserResponse, UserListResponse)


@router.get("/users/{user_id}", response_model=UserResponse)
def get_user(
    user_id: int,
    _admin: str = Depends(get_current_admin_token),
    db: Session = Depends(get_db),
) -> UserResponse:
    return UserResponse.model_validate(get_or_404(db, User, id=user_id, detail="用户不存在。"))


@router.post("/users", response_model=UserResponse)
def create_user(
    payload: UserCreate,
    _admin: str = Depends(get_current_admin_token),
    db: Session = Depends(get_db),
) -> UserResponse:
    if db.query(User).filter(User.username == payload.username).one_or_none():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="用户名已存在。")
    raw_token = generate_activation_token()
    user = User(
        username=payload.username,
        password_hash=None,
        activation_token_hash=hash_activation_token(raw_token),
        can_use=payload.can_use,
        expires_at=payload.expires_at,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    resp = UserResponse.model_validate(user)
    resp.activation_code = encode_activation_code(payload.base_url, raw_token)
    return resp


@router.patch("/users/{user_id}", response_model=UserResponse)
def update_user(
    user_id: int,
    payload: UserUpdate,
    _admin: str = Depends(get_current_admin_token),
    db: Session = Depends(get_db),
) -> UserResponse:
    user = get_or_404(db, User, id=user_id, detail="用户不存在。")
    raw_token: str | None = None
    if payload.regenerate_token:
        raw_token = generate_activation_token()
        user.activation_token_hash = hash_activation_token(raw_token)
    # 显式 null 清空:不放入 apply_partial.exclude,否则正常的"设置未来过期时间"也会被跳过
    if "expires_at" in payload.model_fields_set and payload.expires_at is None:
        user.expires_at = None
    apply_partial(user, payload, exclude={"regenerate_token", "base_url"})
    db.commit()
    resp = UserResponse.model_validate(user)
    if raw_token is not None:
        base_url = payload.base_url or "http://localhost:10620"
        resp.activation_code = encode_activation_code(base_url, raw_token)
    return resp


@router.delete("/users/{user_id}", response_model=dict)
def delete_user(
    user_id: int,
    _admin: str = Depends(get_current_admin_token),
    db: Session = Depends(get_db),
) -> dict:
    db.delete(get_or_404(db, User, id=user_id, detail="用户不存在。"))
    db.commit()
    return {"message": "用户已删除。"}


@router.patch("/users/{user_id}/toggle-active")
def toggle_user_active(
    user_id: int,
    _admin: str = Depends(get_current_admin_token),
    db: Session = Depends(get_db),
) -> UserResponse:
    user = get_or_404(db, User, id=user_id, detail="用户不存在。")
    user.is_active = not user.is_active
    db.commit()
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
def list_model_configs(_admin: str = Depends(get_current_admin_token), db: Session = Depends(get_db)) -> UserModelConfigListResponse:
    return UserModelConfigListResponse(items=[_config_list_item(r) for r in db.query(UserModelConfig).all()])


@router.put("/{user_id}/model-config")
def upsert_model_config(
    user_id: int,
    payload: UserModelConfigRequest,
    _admin: str = Depends(get_current_admin_token),
    db: Session = Depends(get_db),
) -> dict:
    get_or_404(db, User, id=user_id, detail="用户不存在。")
    config = db.query(UserModelConfig).filter(UserModelConfig.user_id == user_id).one_or_none()
    provider_json = merge_provider_json(payload.provider_config, config)
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
def delete_model_config(
    user_id: int,
    _admin: str = Depends(get_current_admin_token),
    db: Session = Depends(get_db),
) -> dict:
    db.delete(get_or_404(db, UserModelConfig, user_id=user_id, detail="模型配置不存在。"))
    db.commit()
    return {"message": "模型配置已删除。"}
