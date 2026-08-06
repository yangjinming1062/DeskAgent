from common import get_router
from components import apply_partial
from components import get_db
from components import naive_utc_now
from components import SETTINGS
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from fastapi import status
from modules.auth import ChangePasswordRequest
from modules.auth import create_access_token
from modules.auth import fingerprint_api_key
from modules.auth import get_current_session
from modules.auth import hash_password
from modules.auth import LoginRecord
from modules.auth import LoginRequest
from modules.auth import public_provider_slots
from modules.auth import RefreshRequest
from modules.auth import TokenResponse
from modules.auth import User
from modules.auth import UserInfo
from modules.auth import UserModelConfig
from modules.auth import UserModelConfigResponse
from modules.auth import UserModelConfigSelfRequest
from modules.auth import verify_password
from modules.system import MessageResponse
from services.llm import merge_provider_json
from services.rate_limit import limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

router = get_router()

# Short-lived ticket TTL: wide enough to open the WS, narrow enough to expire before replay.
WS_TICKET_TTL_SECONDS = 60


@router.post("/login", response_model=TokenResponse)
@limiter.limit(f"{SETTINGS.login_rate_limit_per_minute}/minute", key_func=get_remote_address)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)) -> TokenResponse:
    user = db.query(User).filter(User.username == payload.username, User.is_active.is_(True)).one_or_none()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误。")
    if not user.can_use or (user.expires_at and user.expires_at.date() < naive_utc_now().date()):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="该用户已超过有效使用期限，需要续费后才能继续使用。")

    now = naive_utc_now()
    for record in db.query(LoginRecord).filter(LoginRecord.user_id == user.id, LoginRecord.is_active.is_(True)).all():
        record.is_active = False
        record.logout_at = now
        db.add(record)

    client_ctx_dict = payload.client_context.model_dump(exclude_none=True) if payload.client_context else None
    token, expires_in, token_jti = create_access_token(user_id=user.id, username=user.username, client_context=client_ctx_dict)
    db.add(
        LoginRecord(
            user_id=user.id,
            token_jti=token_jti,
            client_version=payload.client_version,
            ip_address=getattr(request.client, "host", "") or "",
            user_agent=request.headers.get("user-agent", ""),
            is_active=True,
            login_at=now,
            last_seen_at=now,
        )
    )
    db.commit()
    return TokenResponse(access_token=token, expires_in=expires_in, user=UserInfo.model_validate(user))


@router.post("/ws-ticket", response_model=TokenResponse)
def mint_ws_ticket(current: tuple[User, LoginRecord] = Depends(get_current_session)) -> TokenResponse:
    """Mint a short-lived WS-only JWT so the renderer never holds the long-lived bearer."""
    user, _session = current
    token, expires_in, _ = create_access_token(
        user_id=user.id,
        username=user.username,
        expires_in_seconds=WS_TICKET_TTL_SECONDS,
        purpose="ws",
    )
    return TokenResponse(access_token=token, expires_in=expires_in, user=UserInfo.model_validate(user))


@router.post("/refresh", response_model=TokenResponse)
def refresh_session(payload: RefreshRequest, request: Request, current: tuple[User, LoginRecord] = Depends(get_current_session), db: Session = Depends(get_db)) -> TokenResponse:
    user, login_record = current
    now = naive_utc_now()

    # Invalidate old session
    login_record.is_active = False
    login_record.logout_at = now
    db.add(login_record)

    # Issue new token with the updated client_context
    client_ctx_dict = payload.client_context.model_dump(exclude_none=True) if payload.client_context else None
    token, expires_in, token_jti = create_access_token(user_id=user.id, username=user.username, client_context=client_ctx_dict)

    db.add(
        LoginRecord(
            user_id=user.id,
            token_jti=token_jti,
            client_version=payload.client_version,
            ip_address=getattr(request.client, "host", "") or "",
            user_agent=request.headers.get("user-agent", ""),
            is_active=True,
            login_at=now,
            last_seen_at=now,
        )
    )
    db.commit()
    return TokenResponse(access_token=token, expires_in=expires_in, user=UserInfo.model_validate(user))


@router.post("/logout", response_model=MessageResponse)
def logout(current: tuple[User, LoginRecord] = Depends(get_current_session), db: Session = Depends(get_db)) -> MessageResponse:
    _user, login_record = current
    login_record.is_active = False
    login_record.logout_at = naive_utc_now()
    db.add(login_record)
    db.commit()
    return MessageResponse(message="已退出登录。")


@router.post("/change-password", response_model=MessageResponse)
def change_password(payload: ChangePasswordRequest, current: tuple[User, LoginRecord] = Depends(get_current_session), db: Session = Depends(get_db)) -> MessageResponse:
    user, _login_record = current
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="当前密码错误。")
    if payload.current_password == payload.new_password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="新密码必须与当前密码不同。")
    user.password_hash = hash_password(payload.new_password)
    db.add(user)
    db.commit()
    return MessageResponse(message="密码已更新。")


_CAPABILITIES = ("llm", "stt", "tts", "image_gen", "video_gen")


def _build_config_response(cfg: UserModelConfig | None) -> UserModelConfigResponse:
    """Assemble the public view of a user's model config from the raw DB row.

    Only the user's explicitly-set values are returned — empty strings when
    nothing is stored, so the desktop UI never sees server-wide defaults.
    """
    data: dict = {}
    for cap in _CAPABILITIES:
        data[f"{cap}_base_url"] = getattr(cfg, f"{cap}_base_url") or "" if cfg else ""
        data[f"{cap}_api_key_set"] = bool(cfg and getattr(cfg, f"{cap}_api_key"))
        data[f"{cap}_model_name"] = getattr(cfg, f"{cap}_model_name") or "" if cfg else ""
    data["llm_api_key_fingerprint"] = fingerprint_api_key(cfg.llm_api_key) if cfg else ""
    data["provider_config"] = public_provider_slots(cfg.provider_config if cfg else None)
    return UserModelConfigResponse(**data)


@router.get("/model-config", response_model=UserModelConfigResponse)
def model_config(current: tuple[User, LoginRecord] = Depends(get_current_session), db: Session = Depends(get_db)) -> UserModelConfigResponse:
    """Return the user's own per-service model config.

    Only values the user has explicitly set are returned — empty strings
    when nothing is stored, so the desktop UI never sees server-wide
    defaults from ``SETTINGS``. Raw API keys are NEVER returned; the
    renderer only sees ``*_api_key_set`` + ``llm_api_key_fingerprint``.
    """
    user, _session = current
    cfg = db.query(UserModelConfig).filter(UserModelConfig.user_id == user.id).first()
    return _build_config_response(cfg)


@router.put("/model-config", response_model=UserModelConfigResponse)
def update_model_config(
    payload: UserModelConfigSelfRequest,
    current: tuple[User, LoginRecord] = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> UserModelConfigResponse:
    """User self-service model config update.

    Empty ``api_key`` fields keep the existing value (the GET endpoint never
    returns raw keys, so the user cannot re-type them). Empty ``base_url``
    and ``model_name`` clear the field so the provider chain falls back to
    server defaults. Returns the updated public config so the caller can
    refresh badges without a second round-trip.
    """
    user, _session = current
    config = db.query(UserModelConfig).filter(UserModelConfig.user_id == user.id).first()

    # Preserve existing api_keys when the user submits an empty one (the GET
    # endpoint never returns raw keys); ``None`` (JSON null) means "clear".
    for cap in _CAPABILITIES:
        attr = f"{cap}_api_key"
        val = getattr(payload, attr)
        if val is None:
            setattr(payload, attr, "")
        elif not val and config:
            setattr(payload, attr, getattr(config, attr))

    provider_json = merge_provider_json(payload.provider_config, config)

    if config:
        apply_partial(config, payload, exclude=frozenset({"provider_config"}))
        config.provider_config = provider_json
    else:
        data = payload.model_dump()
        data["provider_config"] = provider_json
        config = UserModelConfig(user_id=user.id, **data)
        db.add(config)
    db.commit()
    return _build_config_response(config)
