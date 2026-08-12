from common import get_router
from components import get_db
from fastapi import Depends
from modules.auth import User, get_current_session
from modules.settings import UserSetting
from modules.system import DesktopConfigPutRequest, DesktopConfigResponse
from services.desktop_config import DEFAULT_CONFIG, flatten_config, settings_to_config
from sqlalchemy.orm import Session

router = get_router()


@router.get("", response_model=DesktopConfigResponse)
def get_config(current: tuple[User, object] = Depends(get_current_session), db: Session = Depends(get_db)) -> DesktopConfigResponse:
    user, _ = current
    settings = db.query(UserSetting).filter(UserSetting.user_id == user.id).all()
    return DesktopConfigResponse(config=settings_to_config(settings))


@router.put("", response_model=DesktopConfigResponse)
def put_config(body: DesktopConfigPutRequest, current: tuple[User, object] = Depends(get_current_session), db: Session = Depends(get_db)) -> DesktopConfigResponse:
    user, _ = current

    pairs = flatten_config(body.config)
    for key, value in pairs:
        setting = db.query(UserSetting).filter(UserSetting.user_id == user.id, UserSetting.setting_key == key).first()
        if setting:
            setting.setting_value = value
        else:
            db.add(UserSetting(user_id=user.id, setting_key=key, setting_value=value))

    db.commit()

    settings = db.query(UserSetting).filter(UserSetting.user_id == user.id).all()
    return DesktopConfigResponse(config=settings_to_config(settings))


@router.get("/defaults", response_model=DesktopConfigResponse, dependencies=[Depends(get_current_session)])
def get_config_defaults() -> DesktopConfigResponse:
    return DesktopConfigResponse(config=DEFAULT_CONFIG)
