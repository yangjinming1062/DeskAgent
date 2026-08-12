import asyncio
import importlib
import inspect
import json
import logging
import pkgutil
import threading
import time
from collections.abc import Callable
from typing import Any

import tools
from utils import redact_sensitive_text

from .toolsets import excluded_tool_names

logger = logging.getLogger(__name__)

# Single source of truth for tool result size. ``budget_config.py`` mirrors
# this as ``DEFAULT_RESULT_SIZE_CHARS`` and threads it through every tool
# that has an explicit cap; the value here exists as a final ceiling when a
# tool forgets to set its own. ``get_max_result_size`` is the only public
# read path.
DEFAULT_MAX_RESULT_SIZE_CHARS: int = 100_000


def tool_error(msg: str, **extra) -> str:
    """Return JSON error envelope."""
    return json.dumps({"error": str(msg)} | extra, ensure_ascii=False)


def tool_result(data=None, **kwargs) -> str:
    """Return JSON result envelope."""
    return json.dumps(data if data is not None else kwargs, ensure_ascii=False)


class ToolError(Exception):
    """Raised by async_dispatch when a tool cannot be executed.

    The sandbox RPC entry point `dispatch` swallows this into the legacy
    JSON error envelope string; the WebSocket entry point `async_dispatch`
    lets it propagate so callers can map it to a JSON-RPC error frame.
    """


class ToolRegistry:
    """Singleton router mapping tool names to handlers."""

    def __init__(self) -> None:
        self._tools: dict[str, Callable] = {}
        self._toolset: dict[str, str] = {}
        self._toolset_aliases: dict[str, str] = {}
        self._schemas: dict[str, dict] = {}
        self._check_fns: dict[str, Callable[[], bool]] = {}
        self._check_fn_cache: dict[str, tuple[bool, float]] = {}
        self._check_fn_ttl_seconds: float = 30.0
        self._check_fn_suppression_seconds: float = 60.0
        self._lock = threading.RLock()

    def register_tool(self, name: str, toolset: str | None = None, schema: dict | None = None, check_fn: Callable[[], bool] | None = None, **kwargs: Any) -> Callable:
        if kwargs:
            unknown = ", ".join(sorted(kwargs))
            raise TypeError(f"registry.register_tool got unexpected keyword arguments: {unknown}")
        if schema is None:
            raise TypeError(f"registry.register_tool({name!r}) requires a `schema=` argument (every tool must declare an explicit JSON Schema).")

        def decorator(func: Callable) -> Callable:
            with self._lock:
                self._tools[name] = func
                if toolset:
                    self._toolset[name] = toolset
                self._schemas[name] = schema
                if check_fn is not None:
                    self._check_fns[name] = check_fn
            return func

        return decorator

    def register(
        self, name: str, handler: Callable | None = None, *, toolset: str | None = None, schema: dict | None = None, check_fn: Callable[[], bool] | None = None, **kwargs: Any
    ) -> Callable:
        handler = handler or kwargs.pop("handler", None)
        if not handler:
            raise TypeError("registry.register requires a handler")
        if schema is None:
            raise TypeError(f"registry.register({name!r}) requires a `schema=` argument (every tool must declare an explicit JSON Schema).")
        if kwargs:
            unknown = ", ".join(sorted(kwargs))
            raise TypeError(f"registry.register got unexpected keyword arguments: {unknown}")
        with self._lock:
            self._tools[name] = handler
            if toolset:
                self._toolset[name] = toolset
            self._schemas[name] = schema
            if check_fn is not None:
                self._check_fns[name] = check_fn
        return handler

    def is_tool_available(self, name: str) -> bool:
        """Lazy capability check, TTL-cached with transient-failure suppression.

        Tools without a registered ``check_fn`` are always considered
        available. Tools with a ``check_fn`` are probed on first call and
        cached for ``_check_fn_ttl_seconds`` (30s). If a probe fails
        shortly after a success, the cached "ok" is held for a
        ``_check_fn_suppression_seconds`` grace window (60s) before
        flipping — this matches the hermes-agent pattern, preventing a
        single transient probe failure from silently stripping a tool
        mid-session.

        The suppression path always refreshes ``_check_fn_cache`` with
        the *fresh* timestamp — without that write, every subsequent
        failure within the suppression window reuses the stale success
        tuple, hiding an ongoing outage. The cache row carries
        ``(was_suppressed_ok, last_at)`` semantics: ``was_suppressed_ok``
        stays ``True`` for the suppression window so the LLM sees the
        tool, and the timestamp bumps so the 30 s TTL still expires.
        """
        with self._lock:
            check = self._check_fns.get(name)
            if check is None:
                return name in self._tools
            cached = self._check_fn_cache.get(name)

        if cached is not None:
            last_ok, last_at = cached
            if (time.monotonic() - last_at) < self._check_fn_ttl_seconds:
                return last_ok

        try:
            ok = bool(check())
        except Exception:
            ok = False
        now = time.monotonic()

        with self._lock:
            in_suppression = cached is not None and cached[0] and not ok and (now - cached[1]) < self._check_fn_suppression_seconds
            if in_suppression:
                # Bump the timestamp without flipping the verdict — so
                # the suppression window expires on schedule and the
                # 30 s TTL will fire a real re-probe after it does.
                self._check_fn_cache[name] = (cached[0], now)
                return True
            self._check_fn_cache[name] = (ok, now)
        return ok

    def clear_availability_cache(self) -> None:
        """Drop cached availability results. Called by ``mcp.reload`` etc."""
        with self._lock:
            self._check_fn_cache.clear()

    def deregister(self, name: str) -> None:
        with self._lock:
            self._tools.pop(name, None)
            self._toolset.pop(name, None)
            self._schemas.pop(name, None)

    def register_toolset_alias(self, alias: str, target: str) -> None:
        with self._lock:
            self._toolset_aliases[alias] = target

    def get_toolset_for_tool(self, name: str) -> str | None:
        with self._lock:
            if toolset := self._toolset.get(name):
                return toolset
            return next((alias for alias, target in self._toolset_aliases.items() if target == name), None)

    def get_all_tool_names(self) -> list[str]:
        with self._lock:
            return list(self._tools.keys())

    def get_schemas(self) -> list[dict]:
        with self._lock:
            known = list(self._tools.items())
            explicit = dict(self._schemas)
        schemas = []
        missing: list[str] = []
        for name, _func in known:
            if name in explicit:
                schemas.append(explicit[name])
                continue
            missing.append(name)
        if missing:
            # runner/CLAUDE.md mandates explicit schemas. Failing here turns
            # an undocumented tool into a hard error instead of a silent
            # half-schema reaching the LLM — same blast radius as a typo in
            # the schema would have, just at startup rather than first call.
            raise RuntimeError("Tool(s) registered without an explicit schema: " + ", ".join(sorted(missing)) + ". Add `schema=...` to their register_tool() / register() call.")
        return schemas

    def get_schemas_for_llm(self, disabled_toolset_ids: set[str]) -> list[dict]:
        """Return ``get_schemas()`` filtered by ``disabled_toolset_ids``.

        Symmetric with ``tools/skills/helpers.py::get_disabled_skill_names``
        — reads ``toolsets.disabled`` from the in-memory config via
        ``runner/tools/toolsets/helpers.py``. Used by ``server.py``'s
        ``get_tools`` RPC handler so Desktop never advertises a disabled
        toolset to Backend's LLM-facing schema list.

        Desktop pushes config updates over the WS protocol
        (``deskagent.config.update``); the Runner re-reads the disabled set
        on the next ``get_tools`` call without a process restart.

        MCP tools are always excluded regardless of catalog membership —
        their toggle surface lives in the MCP settings page, not here.

        Holds ``self._lock`` once for the name-set + schema snapshot so a
        concurrent ``register_tool`` between two locks cannot leak.
        """
        with self._lock:
            items = list(self._schemas.items())

        excluded = excluded_tool_names(disabled_toolset_ids, {n for n, _ in items})
        return [schema for name, schema in items if name not in excluded and self.is_tool_available(name)]

    def get_max_result_size(self, default: int | float | None = None) -> int | float:
        return default if default is not None else DEFAULT_MAX_RESULT_SIZE_CHARS

    def dispatch(self, name: str, args: dict, **kwargs) -> str:
        """Sync entry point used by the in-process sandbox RPC (`code_execution_tool`).

        Returns a JSON string per the handler contract — success or error envelope.
        Cannot be called from inside a running event loop with an async tool.
        """
        with self._lock:
            func = self._tools.get(name)
        if not func:
            logger.error(f"Tool {name} not found locally.")
            return json.dumps({"error": f"Tool '{name}' not found locally."})

        try:
            if inspect.iscoroutinefunction(func):
                try:
                    asyncio.get_running_loop()
                except RuntimeError:
                    result = asyncio.run(func(args, **kwargs))
                else:
                    return json.dumps({"error": "Cannot run async tool inside an existing event loop. Refactor to sync or use run_coroutine_threadsafe."})
            else:
                result = func(args, **kwargs)
        except Exception as e:
            logger.error(f"Error executing {name}: {e}")
            return json.dumps({"error": self._sanitize_tool_error(f"Tool execution failed: {type(e).__name__}: {e}")})

        return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)

    async def async_dispatch(self, name: str, args: dict, **kwargs) -> Any:
        """Async entry point used by the WebSocket server. Returns the parsed result;
        raises `ToolError` so the caller can map to a JSON-RPC error frame.
        """
        with self._lock:
            func = self._tools.get(name)
        if not func:
            raise ToolError(f"Tool '{name}' not found locally.")

        try:
            if inspect.iscoroutinefunction(func):
                raw = await func(args, **kwargs)
            else:
                raw = await asyncio.to_thread(func, args, **kwargs)
        except ToolError:
            raise
        except Exception as e:
            logger.error(f"Error executing {name}: {e}")
            raise ToolError(self._sanitize_tool_error(f"Tool execution failed: {type(e).__name__}: {e}")) from e

        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return raw
        return raw

    @staticmethod
    def _sanitize_tool_error(raw: str) -> str:
        """Strip credential fragments. Must never raise to avoid hiding real errors."""
        try:
            return redact_sensitive_text(raw)
        except Exception:
            return raw


def discover_builtin_tools() -> list[str]:
    imported = []
    # Skip modules without tools to avoid loading heavy optional deps.
    for _, name, _ in pkgutil.walk_packages(tools.__path__, tools.__name__ + "."):
        if name.endswith("registry") or name.endswith("mcp_tool"):
            continue
        try:
            importlib.import_module(name)
            imported.append(name)
        except Exception:
            logger.error("Could not import %s", name, exc_info=True)
    return imported


registry = ToolRegistry()
