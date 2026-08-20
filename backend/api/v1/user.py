from common import get_router
from components import SETTINGS, get_db, utc_now
from fastapi import Depends, HTTPException, Request, status
from modules.auth import (
    ActivateRequest,
    LoginRecord,
    RefreshRequest,
    TokenResponse,
    User,
    UserInfo,
    create_access_token,
    decode_activation_code,
    get_current_session,
    hash_activation_token,
)
from modules.system import MessageResponse
from services.rate_limit import limiter
from slowapi.util import get_remote_address
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

WS_TICKET_TTL_SECONDS = 60


router = get_router()

# 短期 ticket TTL：足以开 WS、重放前已过期。


@router.post("/activate", response_model=TokenResponse)
@limiter.limit(f"{SETTINGS.login_rate_limit_per_minute}/minute", key_func=get_remote_address)
async def activate(payload: ActivateRequest, request: Request, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    """用激活码换取会话 JWT：激活码是 base64url JSON {b, t}，t 字段经哈希后按 activation_token_hash 查用户；成功后流程同旧登录（停用旧会话、签发 JWT、写 LoginRecord）。"""
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
async def mint_ws_ticket(current: tuple[User, LoginRecord] = Depends(get_current_session)) -> TokenResponse:
    """签发仅供 WS 的短期 JWT，避免 renderer 持有长寿命 bearer。"""
    user, _session = current
    token, expires_in, _ = create_access_token(user_id=user.id, username=user.username, expires_in_seconds=WS_TICKET_TTL_SECONDS, purpose="ws")
    return TokenResponse(access_token=token, expires_in=expires_in, user=UserInfo.model_validate(user))


@router.post("/refresh", response_model=TokenResponse)
async def refresh_session(
    payload: RefreshRequest, request: Request, current: tuple[User, LoginRecord] = Depends(get_current_session), db: AsyncSession = Depends(get_db)
) -> TokenResponse:
    user, login_record = current
    now = utc_now()

    login_record.is_active = False
    login_record.logout_at = now
    db.add(login_record)

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
