from components import SETTINGS
from modules.auth import UserModelConfig
from sqlalchemy.orm import Session


def resolve_user_llm_config(db: Session, user_id: int) -> dict:
    """Active LLM config for a user — DB row when set, else SETTINGS."""
    config = db.query(UserModelConfig).filter(UserModelConfig.user_id == user_id).first()
    return {
        "api_key": config.llm_api_key if config and config.llm_api_key else SETTINGS.llm_api_key,
        "base_url": config.llm_base_url if config and config.llm_base_url else SETTINGS.llm_base_url,
        "model_name": config.llm_model_name if config and config.llm_model_name else SETTINGS.llm_model_name,
    }
