from common import get_router
from components import SETTINGS, apply_partial, get_db, utc_now
from fastapi import Depends, HTTPException, Request, status
from modules.auth import (
    ActivateRequest,
    LoginRecord,
    RefreshRequest,
    TokenResponse,
    User,
    UserInfo,
    UserModelConfig,
    UserModelConfigResponse,
    UserModelConfigSelfRequest,
    create_access_token,
    decode_activation_code,
    get_current_session,
    hash_activation_token,
)
from modules.system import MessageResponse
from services.auth import CAPABILITIES, build_config_response
from services.llm import merge_provider_json
from services.rate_limit import limiter
from slowapi.util import get_remote_address
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

WS_TICKET_TTL_SECONDS = 60


router = get_router()

# Short-lived ticket TTL: wide enough to open the WS, narrow enough to expire before replay.


@router.post("/activate", response_model=TokenResponse)
@limiter.limit(f"{SETTINGS.login_rate_limit_per_minute}/minute", key_func=get_remote_address)
async def activate(payload: ActivateRequest, request: Request, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    """Exchange an activation code for a session JWT.

    The activation code is a base64url-encoded JSON ``{b, t}`` blob.  The
    ``t`` field is the opaque activation token; we hash it and look up the
    user by ``activation_token_hash``.  On success the flow is identical to
    the old login: deactivate prior sessions, mint a session JWT, write a
    LoginRecord.
    """
    try:
        _base_url, raw_token = decode_activation_code(payload.code)
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="激活码格式无效。")

    token_hash = hash_activation_token(raw_token)
    user = (await db.execute(select(User).where(User.activation_token_hash == token_hash, User.is_active.is_(True)))).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="激活码无效。")
    if user.entitlement_expired:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="该用户已超过有效使用期限，需要续费后才能继续使用。")

    now = utc_now()
    for record in (await db.execute(select(LoginRecord).where(LoginRecord.user_id == user.id, LoginRecord.is_active.is_(True)))).scalars().all():
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
    await db.commit()
    return TokenResponse(access_token=token, expires_in=expires_in, user=UserInfo.model_validate(user))


@router.post("/ws-ticket", response_model=TokenResponse)
def mint_ws_ticket(current: tuple[User, LoginRecord] = Depends(get_current_session)) -> TokenResponse:
    """Mint a short-lived WS-only JWT so the renderer never holds the long-lived bearer."""
    user, _session = current
    token, expires_in, _ = create_access_token(user_id=user.id, username=user.username, expires_in_seconds=WS_TICKET_TTL_SECONDS, purpose="ws")
    return TokenResponse(access_token=token, expires_in=expires_in, user=UserInfo.model_validate(user))


@router.post("/refresh", response_model=TokenResponse)
async def refresh_session(
    payload: RefreshRequest, request: Request, current: tuple[User, LoginRecord] = Depends(get_current_session), db: AsyncSession = Depends(get_db)
) -> TokenResponse:
    user, login_record = current
    now = utc_now()

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
    await db.commit()
    return TokenResponse(access_token=token, expires_in=expires_in, user=UserInfo.model_validate(user))


@router.post("/logout", response_model=MessageResponse)
async def logout(current: tuple[User, LoginRecord] = Depends(get_current_session), db: AsyncSession = Depends(get_db)) -> MessageResponse:
    _user, login_record = current
    login_record.is_active = False
    login_record.logout_at = utc_now()
    db.add(login_record)
    await db.commit()
    return MessageResponse(message="已退出登录。")


@router.get("/model-config", response_model=UserModelConfigResponse)
async def model_config(current: tuple[User, LoginRecord] = Depends(get_current_session), db: AsyncSession = Depends(get_db)) -> UserModelConfigResponse:
    """Return the user's own per-service model config.

    Only values the user has explicitly set are returned — empty strings
    when nothing is stored, so the desktop UI never sees server-wide
    defaults from ``SETTINGS``. Raw API keys are NEVER returned; the
    renderer only sees ``*_api_key_set`` + ``llm_api_key_fingerprint``.
    """
    user, _session = current
    cfg = (await db.execute(select(UserModelConfig).where(UserModelConfig.user_id == user.id))).scalar_one_or_none()
    return build_config_response(cfg)


@router.put("/model-config", response_model=UserModelConfigResponse)
async def update_model_config(
    payload: UserModelConfigSelfRequest, current: tuple[User, LoginRecord] = Depends(get_current_session), db: AsyncSession = Depends(get_db)
) -> UserModelConfigResponse:
    """User self-service model config update.

    Empty ``api_key`` fields keep the existing value (the GET endpoint never
    returns raw keys, so the user cannot re-type them). Empty ``base_url``
    and ``model_name`` clear the field so the provider chain falls back to
    server defaults. Returns the updated public config so the caller can
    refresh badges without a second round-trip.
    """
    user, _session = current
    config = (await db.execute(select(UserModelConfig).where(UserModelConfig.user_id == user.id))).scalar_one_or_none()

    # Preserve existing api_keys when the user submits an empty one (the GET
    # endpoint never returns raw keys); ``None`` (JSON null) means "clear".
    for cap in CAPABILITIES:
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
    await db.commit()
    return build_config_response(config)
