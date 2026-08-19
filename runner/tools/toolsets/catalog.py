from dataclasses import dataclass


@dataclass(frozen=True)
class ToolsetDef:
    id: str
    prefixes: tuple[str, ...] = ()
    extra_tools: tuple[str, ...] = ()


# MCP 工具(``mcp_*``)由运行时过滤器排除 — 见下面的 ``is_mcp_tool``。目录本身只声明非 MCP 形态;
# 运行时过滤器防止未来目录漂移错误地把 MCP 工具塞进来。
TOOLSET_CATALOG: tuple[ToolsetDef, ...] = (
    ToolsetDef(id="browser_automation", prefixes=("browser_",)),
    ToolsetDef(id="file_operations", extra_tools=("read_file", "write_file", "patch", "list_directory", "search_files")),
    ToolsetDef(id="terminal", extra_tools=("terminal",)),
    ToolsetDef(id="code_execution", extra_tools=("execute_code",)),
    ToolsetDef(id="process_management", extra_tools=("process",)),
    ToolsetDef(id="skills_system", extra_tools=("skills_list", "skill_view", "skill_manage")),
    ToolsetDef(id="memory", extra_tools=("memory_retain", "memory_recall", "memory_forget")),
    ToolsetDef(id="web_tools", extra_tools=("web_search", "web_extract", "search_tools")),
    ToolsetDef(id="image_generation", extra_tools=("image_generate",)),
    ToolsetDef(id="text_to_speech", extra_tools=("text_to_speech_tool",)),
    ToolsetDef(id="messaging", extra_tools=("send_message_tool",)),
    ToolsetDef(id="scheduled_tasks", extra_tools=("cronjob",)),
    ToolsetDef(id="agent_delegation", extra_tools=("agent_delegate_tool",)),
    ToolsetDef(id="computer_use", extra_tools=("computer_use",)),
    ToolsetDef(id="media_analysis", extra_tools=("vision_analyze",)),
)


def is_mcp_tool(name: str) -> bool:
    """判断工具名是否属于 MCP 集成(MCP 设置页有自己的开关, 不走 ``toolsets.disabled``)。"""
    return name.startswith("mcp_")


def excluded_tool_names(disabled_ids: set[str], available_tool_names: set[str]) -> set[str]:
    """计算因所属 toolset 被禁用而要从 LLM-facing schema 中隐藏的具体工具名集合。

    ``available_tool_names`` 一般来自 ``registry.get_all_tool_names()`` — 因为前缀展开需要用具体名字过滤,
    所以我们拿实际名字来比对而不是伪造合成条目。MCP 工具(``mcp_*``)无论目录是否列入都无条件排除。
    """
    disabled_prefixes: tuple[str, ...] = tuple(p for d in TOOLSET_CATALOG if d.id in disabled_ids for p in d.prefixes)
    disabled_extras: set[str] = {n for d in TOOLSET_CATALOG if d.id in disabled_ids for n in d.extra_tools}

    excluded: set[str] = set()
    for name in available_tool_names:
        if is_mcp_tool(name):
            excluded.add(name)

            continue

        if name in disabled_extras or any(name.startswith(p) for p in disabled_prefixes):
            excluded.add(name)

    return excluded
