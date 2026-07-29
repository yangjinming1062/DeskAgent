from collections.abc import AsyncIterator
from typing import Any

from openai import AsyncOpenAI

from .base import ChatProvider
from .base import ChatResult
from .base import ChatStreamEvent
from .base import ProviderConfig
from .content import to_provider_content
from .http import get_async_client


class OpenAICompatChatProvider(ChatProvider):
    """Shared base for any provider that speaks the OpenAI Chat Completions
    wire protocol (MiMo, MiniMax when accessed via OpenAI SDK, OpenAI itself,
    OpenRouter, etc.). Subclasses only customize request shaping and event
    emission — the SDK does the heavy lifting."""

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        self._client: AsyncOpenAI = get_async_client(config.api_key, config.base_url)

    def raw_client(self) -> AsyncOpenAI | None:
        return self._client

    async def stream(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        **params: Any,
    ) -> AsyncIterator[ChatStreamEvent]:
        normalized = _normalize_messages(messages)
        stream = await self._client.chat.completions.create(
            model=self.config.model,
            messages=normalized,
            tools=tools,
            stream=True,
            **params,
        )
        async for chunk in stream:
            choice = chunk.choices[0] if chunk.choices else None
            if choice is None:
                continue
            delta = choice.delta
            if delta and delta.content:
                yield ChatStreamEvent(type="delta", text=delta.content, raw=chunk)
            if delta and getattr(delta, "tool_calls", None):
                yield ChatStreamEvent(type="tool_call", tool_calls=list(delta.tool_calls), raw=chunk)
            if getattr(chunk, "usage", None):
                usage = chunk.usage.model_dump() if hasattr(chunk.usage, "model_dump") else dict(chunk.usage)
                yield ChatStreamEvent(type="usage", usage=usage, raw=chunk)
            if choice.finish_reason:
                yield ChatStreamEvent(type="done", finish_reason=choice.finish_reason, raw=chunk)

    async def complete(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        **params: Any,
    ) -> ChatResult:
        normalized = _normalize_messages(messages)
        resp = await self._client.chat.completions.create(
            model=self.config.model,
            messages=normalized,
            tools=tools,
            stream=False,
            **params,
        )
        choice = resp.choices[0] if resp.choices else None
        text = choice.message.content if choice and choice.message else ""
        usage = resp.usage.model_dump() if hasattr(resp.usage, "model_dump") else (dict(resp.usage) if resp.usage else None)
        return ChatResult(text=text or "", usage=usage, raw=resp)


def _normalize_messages(messages: list[dict]) -> list[dict]:
    out: list[dict] = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            out.append({**m, "content": to_provider_content(content)})
        else:
            out.append(m)
    return out