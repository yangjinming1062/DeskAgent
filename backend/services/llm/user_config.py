from modules.auth import UserModelConfig
from sqlalchemy.orm import Session


def resolve_user_llm_config(db: Session, user_id: int) -> dict:
    # Every credential comes from the same chain head the chat path uses,
    # so downstream callers (schedulers, title generation) see one
    # consistent provider. ``db=None`` is allowed for callers that
    # bootstrap without a session.
    from .llm_client import resolve_provider_chain

    config = db.query(UserModelConfig).filter(UserModelConfig.user_id == user_id).first() if db is not None else None
    chain = resolve_provider_chain(db, user_id, "llm", user_cfg=config)
    head = chain[0] if chain else None
    return {
        "api_key": head.api_key if head else "",
        "base_url": head.base_url if head else "",
        "model_name": head.model if head else "",
        "provider_name": head.provider_name if head else "",
    }
