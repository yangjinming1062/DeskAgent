import asyncio
import json
import time
from dataclasses import replace
from typing import Any

from components import SETTINGS, get_logger
from modules.auth import UserModelConfig
from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .llm_debug import log_event, new_call_id, truncate_for_log
from .providers import (
    KNOWN_PROVIDERS,
    SERVICE_DEFAULT_PROVIDER,
    BaseProvider,
    EmbeddingProvider,
    ProviderConfig,
    ServiceType,
    default_base_url,
    default_model_for,
    default_vision_model_for,
    providers_supporting,
    resolve,
    supports_vision,
)
from .providers.http import get_async_client

logger = get_logger(__name__)


def _log_embedding(*, call_id: str, phase: str, provider: str, model: str, user_id: int | None, status: str | None = None, latency_ms: int | None = None, **extras: Any) -> None:
    """Embedding 入口拥有稳定的调用方默认字段（service / call_site），在此处统一注入，使调用方只需关心每次事件的字段。"""
    log_event(
        call_id=call_id,
        service="embedding",
        provider=provider,
        model=model,
        call_site=__name__,
        phase=phase,
        status=status,
        latency_ms=latency_ms,
        user_id=user_id,
        **extras,
    )


def client_for_config(llm_config: dict) -> AsyncOpenAI:
    """从已解析的 ``llm_config`` 字典构建 ``AsyncOpenAI``；缺键时抛 ``KeyError``（可能拿到不完整字典的调用方如后台队列需自行预校验）。"""
    return get_async_client(llm_config["api_key"], llm_config["base_url"])


class MissingLlmConfigError(Exception):
    """用户级 LLM 配置缺失时抛出；调用方按端点协议映射为 400 响应包。"""


async def resolve_service_row(db: AsyncSession | None, user_id: int | None, prefix: str, *, user_cfg: UserModelConfig | None = None) -> tuple[str, str, str]:
    """按服务前缀返回 ``(base_url, api_key, model_name)``；DB 行优先（显式清空也保留），无行或无用户上下文时回退到 ``SETTINGS.<prefix>_*``。``user_cfg`` 允许调用方直接传已加载的行避免重复查询。"""
    config = user_cfg
    if config is None and db is not None and user_id is not None:
        config = (await db.execute(select(UserModelConfig).where(UserModelConfig.user_id == user_id))).scalar_one_or_none()
    return tuple(getattr(config or SETTINGS, f"{prefix}_{suffix}", "") or "" for suffix in ("base_url", "api_key", "model_name"))


def _provider_level_key(name: str) -> str:
    """按供应商名取供应商级 API key。MiniMax 永不继承 MiMo key（避免 host-mismatch 401），其余先 ``SETTINGS.<name>_api_key`` 再回退到 ``SETTINGS.llm_api_key`` 兼容单 key 部署。"""
    if name == "minimax":
        return SETTINGS.minimax_api_key
    return getattr(SETTINGS, f"{name}_api_key", "") or SETTINGS.llm_api_key


def _provider_level_url(name: str, service_type: str) -> str:
    """供应商级 BASE_URL：env 覆盖 → 内置默认 → 非 minimax 时回退 ``SETTINGS.llm_base_url``。MiniMax 路径已内嵌 ``/v1``（``/v1/t2a_v2``、``/v1/voice_design``），httpx 拼接 ``/v1`` 会导致 404；而 OpenAI SDK 在 llm 上需要该后缀，因此仅对非 llm 能力剥离。"""
    explicit = getattr(SETTINGS, f"{name}_base_url", "") or ""
    default = default_base_url(name, service_type)
    if name == "minimax":
        url = explicit or default
        if service_type != "llm" and url.endswith("/v1"):
            url = url[: -len("/v1")]
        return url
    return explicit or default or SETTINGS.llm_base_url


def _resolve_slot_config(name: str, service_type: str, row: tuple[str, str, str]) -> ProviderConfig | None:
    """解析单个供应商在某个能力槽上的 ``ProviderConfig``：依次尝试用户 per-cap 行 → 供应商级 env → 内置默认；未取到 api_key 时返回 ``None`` 让链跳过该槽。"""
    user_base_url, user_api_key, user_model = row

    base_url = user_base_url or _provider_level_url(name, service_type)
    api_key = user_api_key or _provider_level_key(name)
    model = user_model or default_model_for(name, service_type)

    if not api_key or not base_url:
        return None

    return ProviderConfig(base_url=base_url, api_key=api_key, model=model, service_type=ServiceType(service_type), provider_name=name)


def _build_chain_order(service_type: str, user_cfg: UserModelConfig | None = None) -> list[str]:
    """按用户 pin → 全局 pin → ``SETTINGS.providers`` 顺序 → ``SERVICE_DEFAULT_PROVIDER`` 兜底构造 ``service_type`` 的供应商候选链；只保留已注册该服务的供应商。"""
    user_pin = getattr(user_cfg, f"{service_type}_provider", "") if user_cfg else ""
    pin = user_pin or getattr(SETTINGS, f"{service_type}_provider", "") or ""
    if pin and pin not in KNOWN_PROVIDERS:
        raise MissingLlmConfigError(f"{service_type} provider {pin!r} unknown; known: {sorted(KNOWN_PROVIDERS)}")

    base_order = list(SETTINGS.providers) if SETTINGS.providers else [SERVICE_DEFAULT_PROVIDER.get(service_type, "mimo")]
    if pin:
        base_order = [pin] + [name for name in base_order if name != pin]

    supporting = set(providers_supporting(service_type))
    return [name for name in base_order if name in supporting]


async def _load_user_config(db: AsyncSession | None, user_id: int | None) -> UserModelConfig | None:
    if db is None or user_id is None:
        return None
    return (await db.execute(select(UserModelConfig).where(UserModelConfig.user_id == user_id))).scalar_one_or_none()


def _user_provider_slots(user_cfg: UserModelConfig, service_type: str) -> list[ProviderConfig]:
    """从用户 ``provider_config`` JSON 列表中生成 tier-1 链槽，每条记录对应一个槽（仅保留已注册该服务的供应商，且必须同时具备 api_key 与 base_url）；per-cap pin 的供应商置顶。"""
    supporting = set(providers_supporting(service_type))
    slots: list[ProviderConfig] = []
    for entry in json.loads(user_cfg.provider_config or "[]"):
        name = entry.get("name", "")
        if name not in KNOWN_PROVIDERS or name not in supporting:
            continue
        api_key = entry.get("api_key", "") or ""
        base_url = entry.get("base_url", "") or default_base_url(name, service_type)
        model = getattr(user_cfg, f"{service_type}_model_name", "") or default_model_for(name, service_type)
        if api_key and base_url:
            slots.append(ProviderConfig(base_url=base_url, api_key=api_key, model=model, service_type=ServiceType(service_type), provider_name=name))

    pin = getattr(user_cfg, f"{service_type}_provider", "") or ""
    if pin:
        pinned_slots = [s for s in slots if s.provider_name == pin]
        other_slots = [s for s in slots if s.provider_name != pin]
        slots = pinned_slots + other_slots

    return slots


async def resolve_provider_chain(db: AsyncSession | None, user_id: int | None, service_type: str, *, user_cfg: UserModelConfig | None = None) -> list[ProviderConfig]:
    """按用户 provider → 用户 capability → 全局 provider → 全局 capability 四层顺序解析 ``service_type`` 的回退链；前两个具备 api_key 与 base_url 的供应商胜出；空列表由调用方抛 ``MissingLlmConfigError``。"""
    if user_cfg is None:
        user_cfg = await _load_user_config(db, user_id)
    # ``resolve_service_row`` 会查 DB；该行在链内每个槽都相同，提前取一次。
    row = await resolve_service_row(db, user_id, service_type, user_cfg=user_cfg)
    chain: list[ProviderConfig | None] = []
    if user_cfg is not None:
        chain.extend(_user_provider_slots(user_cfg, service_type))
    chain.extend(_resolve_slot_config(name, service_type, row) for name in _build_chain_order(service_type, user_cfg=user_cfg))
    seen: set[str] = set()
    result: list[ProviderConfig] = []
    for cfg in chain:
        if cfg is None or cfg.provider_name in seen:
            continue
        seen.add(cfg.provider_name)
        result.append(cfg)
    return result


async def resolve_provider_config(db: AsyncSession | None, user_id: int | None, service_type: str) -> ProviderConfig:
    """``resolve_provider_chain`` 的薄包装，返回链首元素以保持旧 API 单值契约；链为空时抛 ``MissingLlmConfigError``。"""
    chain = await resolve_provider_chain(db, user_id, service_type)
    if not chain:
        raise MissingLlmConfigError(f"no provider configured for service {service_type!r}")
    return chain[0]


async def resolve_vision_chain(db: AsyncSession | None, user_id: int | None, *, service_type: str = "llm") -> list[ProviderConfig]:
    """``service_type`` 链中所有支持视觉的供应商，model 已替换为视觉模型。"""
    return [
        replace(cfg, model=default_vision_model_for(cfg.provider_name) or cfg.model)
        for cfg in await resolve_provider_chain(db, user_id, service_type)
        if supports_vision(cfg.provider_name)
    ]


def provider_from_config(config: ProviderConfig) -> BaseProvider:
    """从已解析的 config 直接构造供应商实例，跳过 ``provider_for_service`` 的 DB 查询。"""
    cls = resolve(config.service_type, config.provider_name)
    return cls(config)


async def provider_for_service(db: AsyncSession | None, user_id: int | None, service_type: str) -> BaseProvider:
    """解析 config 并实例化供应商，返回链首；多供应商回退请用 ``execute_with_fallback``。"""
    return provider_from_config(await resolve_provider_config(db, user_id, service_type))


async def _resolve_embedding_provider(db: AsyncSession | None, user_id: int | None) -> EmbeddingProvider | None:
    try:
        chain = await resolve_provider_chain(db, user_id, "embedding")
        if not chain:
            # 回退到 chat 供应商并使用 OpenAI 兼容的默认 embedding 模型，但仅限真正暴露 OpenAI 形态 ``/v1/embeddings`` 端点的供应商；原生供应商（minimax 用 ``texts`` 而非 ``input``）会 404 / 返回畸形 body —— 会静默降级语义记忆而不暴露误配。
            from .providers import OPENAI_COMPATIBLE_PROVIDERS

            llm_cfg = await resolve_provider_config(db, user_id, "llm")
            if llm_cfg.provider_name not in OPENAI_COMPATIBLE_PROVIDERS:
                return None
            chain = [
                ProviderConfig(
                    base_url=llm_cfg.base_url,
                    api_key=llm_cfg.api_key,
                    model="text-embedding-3-small",
                    service_type=ServiceType.embedding,
                    provider_name=llm_cfg.provider_name,
                ),
            ]
        provider = provider_from_config(chain[0])
        return provider if isinstance(provider, EmbeddingProvider) else None
    except Exception:
        return None


async def generate_embedding(text: str, user_id: int | None = None, db: AsyncSession | None = None, timeout_seconds: float = 2.0) -> list[float] | None:
    """为单段文本生成 embedding 向量；未配置或失败时返回 None。"""
    if not text or not text.strip():
        return None
    call_id = new_call_id()
    _log_embedding(call_id=call_id, phase="request", provider="(resolving)", model="(resolving)", user_id=user_id, text_preview=truncate_for_log(text)[0], num_texts=1)
    started = time.monotonic()
    try:
        provider = await _resolve_embedding_provider(db, user_id)
        if provider is None:
            _log_embedding(
                call_id=call_id,
                phase="response",
                provider="(none)",
                model="(none)",
                user_id=user_id,
                status="skipped",
                reason="no_provider",
                latency_ms=int((time.monotonic() - started) * 1000),
            )
            return None
        _log_embedding(call_id=call_id, phase="provider_resolved", provider=provider.provider_name, model=getattr(provider.config, "model", ""), user_id=user_id)
        result = await asyncio.wait_for(provider.embed_one(text), timeout=timeout_seconds)
        _log_embedding(
            call_id=call_id,
            phase="response",
            provider=provider.provider_name,
            model=getattr(provider.config, "model", ""),
            user_id=user_id,
            status="success",
            latency_ms=int((time.monotonic() - started) * 1000),
            vector_dim=len(result) if result else 0,
        )
        return result
    except Exception as exc:
        logger.debug("generate_embedding failed", extra={"error": str(exc)})
        _log_embedding(
            call_id=call_id,
            phase="response",
            provider="(unknown)",
            model="(unknown)",
            user_id=user_id,
            status="error",
            latency_ms=int((time.monotonic() - started) * 1000),
            error={"type": type(exc).__name__, "message": str(exc)[:500]},
        )
        return None


async def generate_embeddings(texts: list[str], user_id: int | None = None, db: AsyncSession | None = None, timeout_seconds: float = 5.0) -> list[list[float]] | None:
    """为多段文本生成 embedding 向量列表。"""
    if not texts:
        return []
    call_id = new_call_id()
    _log_embedding(call_id=call_id, phase="request", provider="(resolving)", model="(resolving)", user_id=user_id, text_preview=truncate_for_log(texts[0])[0], num_texts=len(texts))
    started = time.monotonic()
    try:
        provider = await _resolve_embedding_provider(db, user_id)
        if provider is None:
            _log_embedding(
                call_id=call_id,
                phase="response",
                provider="(none)",
                model="(none)",
                user_id=user_id,
                status="skipped",
                reason="no_provider",
                latency_ms=int((time.monotonic() - started) * 1000),
            )
            return None
        _log_embedding(call_id=call_id, phase="provider_resolved", provider=provider.provider_name, model=getattr(provider.config, "model", ""), user_id=user_id)
        result = await asyncio.wait_for(provider.embed(texts), timeout=timeout_seconds)
        _log_embedding(
            call_id=call_id,
            phase="response",
            provider=provider.provider_name,
            model=getattr(provider.config, "model", ""),
            user_id=user_id,
            status="success",
            latency_ms=int((time.monotonic() - started) * 1000),
            vector_dim=len(result[0]) if result else 0,
            num_vectors=len(result) if result else 0,
        )
        return result
    except Exception as exc:
        logger.debug("generate_embeddings failed", extra={"error": str(exc)})
        _log_embedding(
            call_id=call_id,
            phase="response",
            provider="(unknown)",
            model="(unknown)",
            user_id=user_id,
            status="error",
            latency_ms=int((time.monotonic() - started) * 1000),
            error={"type": type(exc).__name__, "message": str(exc)[:500]},
        )
        return None
