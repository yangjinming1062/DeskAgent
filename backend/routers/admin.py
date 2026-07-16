from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import status
from models import User
from models import UserModelConfig
from schemas import UserCreate
from schemas import UserListResponse
from schemas import UserModelConfigListItem
from schemas import UserModelConfigListResponse
from schemas import UserModelConfigRequest
from schemas import UserResponse
from schemas import UserUpdate
from sqlalchemy.orm import Session
from utils import apply_partial
from utils import get_current_admin_token
from utils import get_db
from utils import get_or_404
from utils import hash_password
from utils import list_response

ROUTER = APIRouter(prefix="/admin", tags=["admin"])


@ROUTER.get("/users", response_model=UserListResponse)
def list_users(_admin: str = Depends(get_current_admin_token), db: Session = Depends(get_db)) -> UserListResponse:
    return list_response(db.query(User).order_by(User.id).all(), UserResponse, UserListResponse)


@ROUTER.get("/users/{user_id}", response_model=UserResponse)
def get_user(user_id: int, _admin: str = Depends(get_current_admin_token), db: Session = Depends(get_db)) -> UserResponse:
    return UserResponse.model_validate(get_or_404(db, User, id=user_id, detail="用户不存在。"))


@ROUTER.post("/users", response_model=UserResponse)
def create_user(payload: UserCreate, _admin: str = Depends(get_current_admin_token), db: Session = Depends(get_db)) -> UserResponse:
    if db.query(User).filter(User.username == payload.username).one_or_none():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="用户名已存在。")
    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        display_name=payload.display_name or payload.username,
        can_use=payload.can_use,
        expires_at=payload.expires_at,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return UserResponse.model_validate(user)


@ROUTER.patch("/users/{user_id}", response_model=UserResponse)
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


@ROUTER.delete("/users/{user_id}", response_model=dict)
def delete_user(user_id: int, _admin: str = Depends(get_current_admin_token), db: Session = Depends(get_db)) -> dict:
    db.delete(get_or_404(db, User, id=user_id, detail="用户不存在。"))
    db.commit()
    return {"message": "用户已删除。"}


@ROUTER.patch("/users/{user_id}/toggle-active")
def toggle_user_active(user_id: int, _admin: str = Depends(get_current_admin_token), db: Session = Depends(get_db)) -> UserResponse:
    user = get_or_404(db, User, id=user_id, detail="用户不存在。")
    user.is_active = not user.is_active
    db.commit()
    return UserResponse.model_validate(user)


@ROUTER.get("/model-configs", response_model=UserModelConfigListResponse)
def list_model_configs(_admin: str = Depends(get_current_admin_token), db: Session = Depends(get_db)) -> UserModelConfigListResponse:
    return list_response(db.query(UserModelConfig).all(), UserModelConfigListItem, UserModelConfigListResponse)


@ROUTER.put("/{user_id}/model-config")
def upsert_model_config(user_id: int, payload: UserModelConfigRequest, _admin: str = Depends(get_current_admin_token), db: Session = Depends(get_db)) -> dict:
    get_or_404(db, User, id=user_id, detail="用户不存在。")
    if config := db.query(UserModelConfig).filter(UserModelConfig.user_id == user_id).one_or_none():
        apply_partial(config, payload)
    else:
        db.add(UserModelConfig(user_id=user_id, **payload.model_dump()))
    db.commit()
    return {"message": "模型配置已更新。"}


@ROUTER.delete("/{user_id}/model-config")
def delete_model_config(user_id: int, _admin: str = Depends(get_current_admin_token), db: Session = Depends(get_db)) -> dict:
    db.delete(get_or_404(db, UserModelConfig, user_id=user_id, detail="模型配置不存在。"))
    db.commit()
    return {"message": "模型配置已删除。"}
