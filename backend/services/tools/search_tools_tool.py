import json

from .registry import REGISTRY, schema_name

SEARCH_TOOLS_SCHEMA = {
    "name": "search_tools",
    "description": "Search for available tools and plugins in the system by keyword or intent. Call this when you need a capability you don't currently have (e.g., 'browser', 'database', 'github'). Returns a list of matching tool names and descriptions. The system will automatically enable any tools returned by this search for your immediate use.",
    "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "Keyword or intent to search for."}}, "required": ["query"]},
}


def search_tools_tool(query: str, **kwargs) -> str:
    user_id = kwargs.get("user_id")
    if user_id is None:
        # 由 ToolsRegistry.execute_backend_tool 注入保留键；直接单元测试调用时缺失，返回空结果而非崩溃。
        return json.dumps({"matched_tools": []}, ensure_ascii=False)

    # 与 build_turn_inputs 同源过滤——否则会向 LLM 暴露受门控的 schema（如未配置 Tavily key 的 web_extract）。
    user_settings = kwargs.get("user_settings") or {}
    results = []
    query_lower = query.lower()
    for schema in REGISTRY.get_all_schemas(user_id, user_settings=user_settings):
        name = schema_name(schema)
        desc = schema.get("description", "")
        if query_lower in name.lower() or query_lower in desc.lower():
            results.append({"name": name, "description": desc})
    return json.dumps({"matched_tools": results}, ensure_ascii=False)


REGISTRY.register("search_tools", SEARCH_TOOLS_SCHEMA, search_tools_tool)
