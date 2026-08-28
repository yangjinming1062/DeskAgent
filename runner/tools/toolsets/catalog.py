from dataclasses import dataclass


@dataclass(frozen=True)
class ToolsetDef:
    id: str
    prefixes: tuple[str, ...] = ()
    extra_tools: tuple[str, ...] = ()


# 工具集 id 的权威枚举见 PROTOCOL.md §2.2；本目录只做 id → runner 侧工具名/前缀的映射。
TOOLSET_CATALOG: tuple[ToolsetDef, ...] = (
    ToolsetDef(id="browser_automation", prefixes=("browser_",)),
    ToolsetDef(id="file_operations", extra_tools=("read_file", "write_file", "patch", "list_directory", "search_files")),
    ToolsetDef(id="terminal", extra_tools=("terminal",)),
    ToolsetDef(id="code_execution", extra_tools=("execute_code",)),
    ToolsetDef(id="process_management", extra_tools=("process",)),
    ToolsetDef(id="skills_system", extra_tools=("skills_list", "skill_view", "skill_manage")),
    ToolsetDef(id="computer_use", extra_tools=("computer_use",)),
    ToolsetDef(id="media_analysis", extra_tools=("vision_analyze",)),
)


def excluded_tool_names(disabled_ids: set[str], available_tool_names: set[str]) -> set[str]:
    """计算因所属 toolset 被禁用而要从 LLM-facing schema 中隐藏的具体工具名集合。

    ``available_tool_names`` 一般来自 ``registry.get_all_tool_names()`` — 因为前缀展开需要用具体名字过滤,
    所以我们拿实际名字来比对而不是伪造合成条目。
    """
    disabled_prefixes: tuple[str, ...] = tuple(p for d in TOOLSET_CATALOG if d.id in disabled_ids for p in d.prefixes)
    disabled_extras: set[str] = {n for d in TOOLSET_CATALOG if d.id in disabled_ids for n in d.extra_tools}

    excluded: set[str] = set()
    for name in available_tool_names:
        if name in disabled_extras or any(name.startswith(p) for p in disabled_prefixes):
            excluded.add(name)

    return excluded
