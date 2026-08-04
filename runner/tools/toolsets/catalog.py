from dataclasses import dataclass


@dataclass(frozen=True)
class ToolsetDef:
    id: str
    prefixes: tuple[str, ...] = ()
    extra_tools: tuple[str, ...] = ()


# MCP tools (``mcp_*``) are excluded at the runtime filter — see
# `is_mcp_tool` below. The catalog itself only declares non-MCP shapes; the
# runtime filter guards against future catalog drift that might erroneously
# list an MCP tool.
TOOLSET_CATALOG: tuple[ToolsetDef, ...] = (
    ToolsetDef(id="browser_automation", prefixes=("browser_",)),
    ToolsetDef(
        id="file_operations",
        extra_tools=("read_file", "write_file", "patch", "list_directory", "search_files"),
    ),
    ToolsetDef(id="terminal", extra_tools=("terminal",)),
    ToolsetDef(id="code_execution", extra_tools=("execute_code",)),
    ToolsetDef(id="process_management", extra_tools=("process",)),
    ToolsetDef(id="skills_system", extra_tools=("skills_list", "skill_view", "skill_manage")),
    ToolsetDef(
        id="memory",
        extra_tools=("memory_retain", "memory_recall", "memory_forget"),
    ),
    ToolsetDef(
        id="web_tools",
        extra_tools=("web_search", "web_extract", "search_tools"),
    ),
    ToolsetDef(id="image_generation", extra_tools=("image_generate",)),
    ToolsetDef(id="text_to_speech", extra_tools=("text_to_speech_tool",)),
    ToolsetDef(id="messaging", extra_tools=("send_message_tool",)),
    ToolsetDef(id="scheduled_tasks", extra_tools=("cronjob",)),
    ToolsetDef(id="agent_delegation", extra_tools=("agent_delegate_tool",)),
    ToolsetDef(id="computer_use", extra_tools=("computer_use",)),
    ToolsetDef(id="media_analysis", extra_tools=("vision_analyze",)),
)


def is_mcp_tool(name: str) -> bool:
    return name.startswith("mcp_")


def excluded_tool_names(disabled_ids: set[str], available_tool_names: set[str]) -> set[str]:
    """Compute the set of concrete tool names that should be hidden from the
    LLM-facing schema because their owning toolset is disabled.

    `available_tool_names` is typically ``registry.get_all_tool_names()`` —
    the prefix expansion needs concrete names to filter, so we resolve
    `prefixes` against this set rather than fabricating synthetic entries.
    MCP tools (`mcp_*`) are unconditionally excluded regardless of catalog
    membership; their toggle surface is the MCP settings page, not this one.
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
