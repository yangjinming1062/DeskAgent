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

from utils import redact_sensitive_text

import tools

from .toolsets import excluded_tool_names

logger = logging.getLogger(__name__)

# 工具结果大小的唯一真源; ``budget_config.py`` 把它下发给每个有显式上限的工具。
# ``get_max_result_size`` 是唯一的公共读取入口。
DEFAULT_MAX_RESULT_SIZE_CHARS: int = 100_000


def tool_error(msg: str, **extra) -> str:
    """构造一个 JSON 错误信封。"""
    return json.dumps({"error": str(msg)} | extra, ensure_ascii=False)


def tool_result(data=None, **kwargs) -> str:
    """构造一个 JSON 结果信封。"""
    return json.dumps(data if data is not None else kwargs, ensure_ascii=False)


class ToolError(Exception):
    """``async_dispatch`` 在工具无法执行时抛出。

    沙箱 RPC 入口 ``dispatch`` 会吞掉该异常并转换为遗留的 JSON 错误信封; WS 入口
    ``async_dispatch`` 则让它上抛, 以便调用方映射成 JSON-RPC 错误帧。
    """


class ToolRegistry:
    """把工具名路由到处理函数的单例注册中心。"""

    def __init__(self) -> None:
        self._tools: dict[str, Callable] = {}
        self._toolset: dict[str, str] = {}
        self._toolset_aliases: dict[str, str] = {}
        self._schemas: dict[str, dict] = {}
        self._check_fns: dict[str, Callable[[], bool]] = {}
        self._check_fn_cache: dict[str, tuple[bool, float, float]] = {}
        self._check_fn_ttl_seconds: float = 30.0
        self._check_fn_suppression_seconds: float = 60.0
        self._lock = threading.RLock()

    def register_tool(self, name: str, toolset: str | None = None, schema: dict | None = None, check_fn: Callable[[], bool] | None = None, **kwargs: Any) -> Callable:
        """装饰器形式注册工具(``schema`` 必填, 表达 JSON Schema)。"""
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
        self,
        name: str,
        handler: Callable | None = None,
        *,
        toolset: str | None = None,
        schema: dict | None = None,
        check_fn: Callable[[], bool] | None = None,
        **kwargs: Any,
    ) -> Callable:
        """直接调用形式注册工具(handler 与 schema 都必填)。"""
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
        """能力探测的惰性检查 + TTL 缓存 + 瞬时失败抑制。

        没有 ``check_fn`` 的工具始终视为可用; 有 ``check_fn`` 的在第一次探测后缓存 ``_check_fn_ttl_seconds``(30s)。
        在最近一次 *成功* 探测 ``_check_fn_suppression_seconds``(60s)窗口内的失败保留上一次"可用"的判定,
        这样一次瞬时抖动不会把工具从会话中途悄悄摘掉。抑制截止时刻只锚定成功: 持续失败的探测
        在窗口过期后才翻为不可用 — 失败不会延长窗口。缓存行是 ``(last_ok, probed_at, suppress_until)``。
        """
        with self._lock:
            check = self._check_fns.get(name)
            if check is None:
                return name in self._tools
            cached = self._check_fn_cache.get(name)

        now = time.monotonic()
        if cached is not None:
            last_ok, probed_at, suppress_until = cached
            if now - probed_at < self._check_fn_ttl_seconds:
                return last_ok
            if last_ok and now < suppress_until:
                return True

        try:
            ok = bool(check())
        except Exception:
            ok = False

        now = time.monotonic()
        with self._lock:
            prior = self._check_fn_cache.get(name)
            suppress_until = now + self._check_fn_suppression_seconds if ok else (prior[2] if prior else now)
            self._check_fn_cache[name] = (ok, now, suppress_until)
        return ok

    def clear_availability_cache(self) -> None:
        """丢弃缓存的能力探测结果(由 ``mcp.reload`` 等触发)。"""
        with self._lock:
            self._check_fn_cache.clear()

    def deregister(self, name: str) -> None:
        """注销某个工具, 一并清掉它的能力探测缓存(否则 ``mcp.reload`` 重注册会复用旧结果)。"""
        with self._lock:
            self._tools.pop(name, None)
            self._toolset.pop(name, None)
            self._schemas.pop(name, None)
            # 不清掉的话 mcp.reload 的重新注册会静默复用旧 check_fn 及其缓存结果。
            self._check_fns.pop(name, None)
            self._check_fn_cache.pop(name, None)

    def register_toolset_alias(self, alias: str, target: str) -> None:
        """注册工具集别名, 让 ``get_toolset_for_tool`` 能反查到工具所属集。"""
        with self._lock:
            self._toolset_aliases[alias] = target

    def get_toolset_for_tool(self, name: str) -> str | None:
        """返回工具所属的 toolset, 也支持别名反查。"""
        with self._lock:
            if toolset := self._toolset.get(name):
                return toolset
            return next((alias for alias, target in self._toolset_aliases.items() if target == name), None)

    def get_all_tool_names(self) -> list[str]:
        """返回已注册工具名的快照(用于 ``get_schemas_for_llm`` 等过滤流程)。"""
        with self._lock:
            return list(self._tools.keys())

    def get_schemas(self) -> list[dict]:
        """收集所有已注册工具的 JSON Schema; 未声明 schema 的工具直接抛错(启动期硬错误优于 LLM 拿到半截 schema)。"""
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
            # 在这里硬失败, 比让一个未 schema 的工具在第一次调用时再爆更有用 — 爆炸半径相同, 但发现时机提前到启动期。
            raise RuntimeError("Tool(s) registered without an explicit schema: " + ", ".join(sorted(missing)) + ". Add `schema=...` to their register_tool() / register() call.")
        return schemas

    def get_schemas_for_llm(self, disabled_toolset_ids: set[str]) -> list[dict]:
        """根据 ``toolsets.disabled`` 过滤后的 schema 列表 — 由 ``server.py`` 的 ``get_tools`` RPC 用, 防止 Desktop 把禁用 toolset 喂给后端 LLM。

        MCP 工具无论是否在禁用名单中始终排除(它们的开关在 MCP 设置页, 不在这里); 一次性
        持锁获取 schema 快照, 避免与并发的 ``register_tool`` 互相越界。
        """
        with self._lock:
            items = list(self._schemas.items())

        excluded = excluded_tool_names(disabled_toolset_ids, {n for n, _ in items})
        return [schema for name, schema in items if name not in excluded and self.is_tool_available(name)]

    def get_max_result_size(self, default: int | float | None = None) -> int | float:
        """返回工具结果的大小上限; 未指定时回落到默认。"""
        return default if default is not None else DEFAULT_MAX_RESULT_SIZE_CHARS

    def dispatch(self, name: str, args: dict, **kwargs) -> str:
        """同步入口, 供沙箱内 RPC(``code_execution_tool``)调用; 返回 JSON 字符串。注意: 不可在已运行的事件循环内调用异步工具。"""
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
        """异步入口, 供 WebSocket 服务器调用; 抛出 ``ToolError`` 让调用方映射 JSON-RPC 错误帧。"""
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
        """剥离凭据片段; 此函数绝不能抛, 否则会掩盖真正的错误。"""
        try:
            return redact_sensitive_text(raw)
        except Exception:
            return raw


def discover_builtin_tools() -> list[str]:
    """遍历 ``tools/`` 子包并 import, 作为注册副作用触发每个工具模块的 ``register_tool``。"""
    imported = []
    # 跳过不含工具的模块(registry / mcp_tool)以免触发重型可选依赖加载。
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
