import json

from modules.auth import ProviderSlot, UserModelConfig
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .llm_client import resolve_provider_chain


def merge_provider_json(slots: list[ProviderSlot], existing: UserModelConfig | None) -> str:
    """把供应商槽位序列化为 JSON；空 api_key 保留已有 key（前端看不见原值，"留空"必须等于"无修改"）。"""
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


async def resolve_user_llm_config(db: AsyncSession | None, user_id: int) -> UserLlmConfig:
    # 所有凭据都来自 chat 路径同一链头，下游调用方（scheduler、title 生成）看到一致的供应商。``db=None`` 允许无 session 的启动场景。
    config = (await db.execute(select(UserModelConfig).where(UserModelConfig.user_id == user_id))).scalar_one_or_none() if db is not None else None
    chain = await resolve_provider_chain(db, user_id, "llm", user_cfg=config)
    head = chain[0] if chain else None
    return UserLlmConfig(
        api_key=head.api_key if head else "",
        base_url=head.base_url if head else "",
        model_name=head.model if head else "",
        provider_name=head.provider_name if head else "",
    )
