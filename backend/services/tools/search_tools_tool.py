import json

from .registry import REGISTRY, schema_name

SEARCH_TOOLS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_tools",
        "description": "Search for available tools and plugins in the system by keyword or intent. Call this when you need a capability you don't currently have (e.g., 'browser', 'database', 'github'). Returns a list of matching tool names and descriptions. The system will automatically enable any tools returned by this search for your immediate use.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Keyword or intent to search for.",
                }
            },
            "required": ["query"],
        },
    },
}


def search_tools_tool(query: str, **kwargs) -> str:
    user_id = kwargs.get("user_id")
    if user_id is None:
        # Reserved-injected by ToolsRegistry.execute_backend_tool; absent on
        # direct unit-test calls. Empty result, not crash.
        return json.dumps({"matched_tools": []}, ensure_ascii=False)

    # Same gate as _build_turn_inputs — without this the tool would surface
    # gated schemas (e.g. web_extract without a Tavily key) to the LLM.
    user_settings = kwargs.get("user_settings") or {}
    results = []
    query_lower = query.lower()
    for schema in REGISTRY.get_all_schemas(user_id, user_settings=user_settings):
        name = schema_name(schema)
        block = schema.get("function") if "function" in schema else schema
        desc = block.get("description", "")
        if query_lower in name.lower() or query_lower in desc.lower():
            results.append({"name": name, "description": desc})
    return json.dumps({"matched_tools": results}, ensure_ascii=False)


REGISTRY.register("search_tools", SEARCH_TOOLS_SCHEMA, search_tools_tool)
