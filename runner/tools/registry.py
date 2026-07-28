import asyncio
import importlib
import inspect
import json
import logging
import pkgutil
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import tools
from tools.toolsets import excluded_tool_names
from utils import redact_sensitive_text

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

    def __init__(self):
        self._tools: dict[str, Callable] = {}
        self._toolset: dict[str, str] = {}
        self._toolset_aliases: dict[str, str] = {}
        self._schemas: dict[str, dict] = {}
        self._lock = threading.RLock()

    def register_tool(self, name: str, toolset: str | None = None, schema: dict | None = None, **kwargs: Any) -> Callable:
        if kwargs:
            unknown = ", ".join(sorted(kwargs))
            raise TypeError(f"registry.register_tool got unexpected keyword arguments: {unknown}")
        if schema is None:
            raise TypeError(f"registry.register_tool({name!r}) requires a `schema=` argument. runner/CLAUDE.md mandates explicit schemas for every tool.")

        def decorator(func: Callable) -> Callable:
            with self._lock:
                self._tools[name] = func
                if toolset:
                    self._toolset[name] = toolset
                self._schemas[name] = schema
            return func

        return decorator

    def register(
        self,
        name: str,
        handler: Callable | None = None,
        *,
        toolset: str | None = None,
        schema: dict | None = None,
        **kwargs: Any,
    ) -> Callable:
        handler = handler or kwargs.pop("handler", None)
        if not handler:
            raise TypeError("registry.register requires a handler")
        if schema is None:
            raise TypeError(f"registry.register({name!r}) requires a `schema=` argument. runner/CLAUDE.md mandates explicit schemas for every tool.")
        if kwargs:
            unknown = ", ".join(sorted(kwargs))
            raise TypeError(f"registry.register got unexpected keyword arguments: {unknown}")
        with self._lock:
            self._tools[name] = handler
            if toolset:
                self._toolset[name] = toolset
            self._schemas[name] = schema
        return handler

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
        for name, func in known:
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
        — reads ``toolsets.disabled`` from ``$DESKAGENT_HOME/config.yaml`` via
        ``runner/tools/toolsets/helpers.py``. Used by ``server.py``'s
        ``get_tools`` RPC handler so Desktop never advertises a disabled
        toolset to Backend's LLM-facing schema list.

        Designed to be paired with ``patchAndCommit`` + ``restartRunnerBridge``
        in Desktop's IPC: writes go through the shared atomic-write lock,
        then the bridge cold-starts and the Runner re-fetches this method.

        MCP tools are always excluded regardless of catalog membership —
        their toggle surface lives in the MCP settings page, not here.

        Holds ``self._lock`` once for the name-set + schema snapshot so a
        concurrent ``register_tool`` between two locks cannot leak.
        """
        with self._lock:
            items = list(self._schemas.items())

        excluded = excluded_tool_names(disabled_toolset_ids, {n for n, _ in items})
        return [schema for _, schema in items if _ not in excluded]

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
        except Exception as e:
            logger.debug(f"Could not import {name}: {e}")
    return imported


registry = ToolRegistry()
