import json

from modules.auth import ProviderSlot, UserModelConfig
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session


def merge_provider_json(
    slots: list[ProviderSlot],
    existing: UserModelConfig | None,
) -> str:
    """Serialize provider slots to JSON, preserving existing keys on empty submit.

    An empty ``api_key`` keeps the existing key for that provider — the caller
    can't see the raw value, so "leave blank" must mean "no change".
    Shared by the admin and user self-service model-config endpoints.
    """
    prev = {s["name"]: s.get("api_key", "") for s in json.loads(existing.provider_config or "[]")} if existing else {}
    out = []
    for slot in slots:
        d = slot.model_dump()
        if not d.get("api_key") and d["name"] in prev:
            d["api_key"] = prev[d["name"]]
        out.append(d)
    return json.dumps(out)


class UserLlmConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    api_key: str = ""
    base_url: str = ""
    model_name: str = ""
    provider_name: str = ""

    def get(self, key: str, default: str | None = None) -> str | None:
        return getattr(self, key, default)

    def __getitem__(self, item: str) -> str:
        if hasattr(self, item):
            return getattr(self, item)
        raise KeyError(item)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, dict):
            d = self.model_dump()
            return all(d.get(k) == v for k, v in other.items())
        return super().__eq__(other)


def resolve_user_llm_config(db: Session, user_id: int) -> UserLlmConfig:
    # Every credential comes from the same chain head the chat path uses,
    # so downstream callers (schedulers, title generation) see one
    # consistent provider. ``db=None`` is allowed for callers that
    # bootstrap without a session.
    from .llm_client import resolve_provider_chain

    config = db.query(UserModelConfig).filter(UserModelConfig.user_id == user_id).first() if db is not None else None
    chain = resolve_provider_chain(db, user_id, "llm", user_cfg=config)
    head = chain[0] if chain else None
    return UserLlmConfig(
        api_key=head.api_key if head else "",
        base_url=head.base_url if head else "",
        model_name=head.model if head else "",
        provider_name=head.provider_name if head else "",
    )
