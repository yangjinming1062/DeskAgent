from common import get_router
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
from modules.auth import RefreshRequest
from modules.auth import TokenResponse
from modules.auth import User
from modules.auth import UserInfo
from modules.auth import UserModelConfigResponse
from modules.auth import verify_password
from modules.system import MessageResponse
from services.llm import resolve_service_row
from services.rate_limit import limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

router = get_router()


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


@router.get("/model-config", response_model=UserModelConfigResponse)
def model_config(current: tuple[User, LoginRecord] = Depends(get_current_session), db: Session = Depends(get_db)) -> UserModelConfigResponse:
    """Return the user's effective per-service model config.

    Raw API keys are NEVER returned — the renderer only sees a
    ``*_api_key_set`` boolean per service so the UI can confirm whether a
    key is on file. Full keys are read server-side when the LLM/STT/TTS/
    image-gen client is actually built (see ``core/llm/client_for_service``).
    """
    user, _session = current
    llm_base_url, llm_api_key, llm_model_name = resolve_service_row(db, user.id, "llm")
    stt_base_url, stt_api_key, stt_model_name = resolve_service_row(db, user.id, "stt")
    tts_base_url, tts_api_key, tts_model_name = resolve_service_row(db, user.id, "tts")
    img_base_url, img_api_key, img_model_name = resolve_service_row(db, user.id, "image_gen")
    vid_base_url, vid_api_key, vid_model_name = resolve_service_row(db, user.id, "video_gen")

    return UserModelConfigResponse(
        llm_base_url=llm_base_url,
        llm_api_key_fingerprint=fingerprint_api_key(llm_api_key),
        llm_api_key_set=bool(llm_api_key),
        llm_model_name=llm_model_name,
        stt_base_url=stt_base_url,
        stt_api_key_set=bool(stt_api_key),
        stt_model_name=stt_model_name,
        tts_base_url=tts_base_url,
        tts_api_key_set=bool(tts_api_key),
        tts_model_name=tts_model_name,
        image_gen_base_url=img_base_url,
        image_gen_api_key_set=bool(img_api_key),
        image_gen_model_name=img_model_name,
        video_gen_base_url=vid_base_url,
        video_gen_api_key_set=bool(vid_api_key),
        video_gen_model_name=vid_model_name,
    )
