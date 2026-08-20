import inspect
import json
from collections.abc import Callable
from typing import Any

from components import get_logger, tool_error

from .extract_provider import resolve_extract_provider

logger = get_logger(__name__)


def schema_name(schema: dict[str, Any]) -> str:
    if "function" in schema:
        return schema["function"]["name"]
    return schema["name"]


# 工具的 ``user_id`` / ``llm_config`` / ``user_settings`` 是正常 kwargs，被注入时从 LLM 提供的 args 中静默剔除，避免受 LLM 注入。
RESERVED_KEYS = frozenset({"user_id", "llm_config", "user_settings"})

AvailabilityCheck = Callable[[dict[str, str]], bool]


def _always_available(_user_settings: dict[str, str]) -> bool:
    return True


def _web_extract_available(user_settings: dict[str, str]) -> bool:
    """与 ``web_extract_tool`` 运行时检查互为镜像，让无法服务 extract 的供应商不暴露对应 schema。"""
    try:
        provider = resolve_extract_provider(user_settings)
        return provider.is_available() and provider.supports_extract()
    except Exception as e:
        logger.warning("web_extract availability check failed", extra={"error_msg": str(e)})
        return False


def _build_backend_entry(func: Callable, availability_check: AvailabilityCheck) -> dict[str, Any]:
    return {"func": func, "is_coro": inspect.iscoroutinefunction(func), "availability_check": availability_check}


# 知名可用性谓词，工具模块注册时可引用。
ALWAYS_AVAILABLE: AvailabilityCheck = _always_available
WEB_EXTRACT_AVAILABILITY: AvailabilityCheck = _web_extract_available


class ToolsRegistry:
    """三个桶：backend（进程内）、memory（DB）、runner（按用户 IPC）。"""

    def __init__(self) -> None:
        self._backend_tools: dict[str, dict[str, Any]] = {}
        self._memory_tools: dict[str, dict[str, Any]] = {}
        self._runner_tools: dict[int, dict[str, dict[str, Any]]] = {}

    def register(self, name: str, schema: dict, func: Callable, availability_check: AvailabilityCheck = _always_available) -> None:
        """添加（或替换）一个 backend 工具；导入时幂等，重复 ``import`` 不会报错。"""
        entry = _build_backend_entry(func, availability_check)
        entry["schema"] = schema
        self._backend_tools[name] = entry

    def register_memory(self, name: str, schema: dict) -> None:
        """添加 memory 工具（无函数——由 ``NativeMemory`` 派发），以扁平 ``{name: schema}`` 存储。"""
        self._memory_tools[name] = schema

    def update_runner_tools(self, user_id: int, schemas: list[dict[str, Any]]) -> None:
        self._runner_tools[user_id] = {schema_name(schema): schema for schema in schemas}
        logger.info("Updated runner tools", extra={"user_id": user_id, "schema_count": len(schemas)})

    def clear_runner_tools(self, user_id: int) -> None:
        if user_id in self._runner_tools:
            del self._runner_tools[user_id]
            logger.info("Cleared runner tools", extra={"user_id": user_id})

    def has_runner_tools(self, user_id: int) -> bool:
        return bool(self._runner_tools.get(user_id))

    def get_all_schemas(self, user_id: int, user_settings: dict[str, str] | None = None) -> list[dict[str, Any]]:
        # ``user_settings=None`` 保留「无条件包含全部」的旧行为，给子代理和 pre-DB 调用方使用；传入 settings 时由各工具的 availability_check 决定可见性，谓词抛错则隐藏该工具（fail-closed），避免一个 bug 把整次调用拖到 500。
        schemas: list[dict[str, Any]] = []
        if user_settings is None:
            schemas.extend(e["schema"] for e in self._backend_tools.values())
        else:
            for entry in self._backend_tools.values():
                try:
                    if entry["availability_check"](user_settings):
                        schemas.append(entry["schema"])
                except Exception as e:
                    logger.warning(
                        "availability_check raised; hiding tool",
                        extra={"tool_name": entry["schema"].get("name") or entry["schema"].get("function", {}).get("name"), "error_msg": str(e)},
                    )
        schemas.extend(self._memory_tools.values())
        schemas.extend(self._runner_tools.get(user_id, {}).values())
        return schemas

    def get_all_tool_names(self, user_id: int, user_settings: dict[str, str] | None = None) -> list[str]:
        return [schema_name(s) for s in self.get_all_schemas(user_id, user_settings=user_settings)]

    def _lookup(self, user_id: int, tool_name: str) -> tuple[str, dict[str, Any]] | None:
        if (entry := self._backend_tools.get(tool_name)) is not None:
            return "backend", entry
        if (schema := self._memory_tools.get(tool_name)) is not None:
            return "memory", {"schema": schema}
        if (entry := self._runner_tools.get(user_id, {}).get(tool_name)) is not None:
            return "runner", entry
        return None

    def get_schema(self, user_id: int, tool_name: str) -> dict[str, Any] | None:
        return found[1]["schema"] if (found := self._lookup(user_id, tool_name)) else None

    def get_location(self, user_id: int, tool_name: str) -> str:
        return found[0] if (found := self._lookup(user_id, tool_name)) else "unknown"

    async def execute_backend_tool(self, name: str, args: dict, **context) -> str:
        entry = self._backend_tools.get(name)
        if entry is None:
            return tool_error(f"Tool {name} not found in backend registry.")

        # 保留键始终优先——见模块顶部 RESERVED_KEYS。
        call_args: dict[str, Any] = {k: v for k, v in (args or {}).items() if k not in RESERVED_KEYS}
        for k, v in context.items():
            call_args.setdefault(k, v)

        func = entry["func"]
        try:
            result = await func(**call_args) if entry["is_coro"] else func(**call_args)
        except Exception as e:
            logger.error("Error executing backend tool", extra={"tool_name": name, "error": str(e)})
            return tool_error(str(e))

        return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)


REGISTRY = ToolsRegistry()
