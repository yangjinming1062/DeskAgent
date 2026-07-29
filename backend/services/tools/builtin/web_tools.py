import asyncio
import json

from components import coerce_int
from components import get_logger
from components import tool_error
from components import unquote_user_setting

from ...llm.llm_client import client_for_config
from ...llm.llm_retry import call_with_retry
from ..extract_provider import _DEFAULT_BY_KIND
from ..extract_provider import _get_provider
from ..extract_provider import resolve_extract_provider

logger = get_logger(__name__)


async def _summarize_doc(client, model_name: str, doc: dict) -> None:
    content = doc.get("content", "")
    if not content or len(content) <= 1000:
        return
    try:
        response = await call_with_retry(
            client,
            model=model_name,
            messages=[
                {"role": "system", "content": "Summarize the web content and extract key information in markdown format. Be concise."},
                {"role": "user", "content": f"URL: {doc.get('url')}\nContent: {content[:50000]}"},
            ],
            temperature=0.1,
        )
        doc["content"] = response.choices[0].message.content
    except Exception as e:
        # Per-doc catch: a single bad doc must not abort the whole gather
        # batch (httpx, JSON parse, LLMRuntimeError, or empty choices all land here).
        logger.warning("Failed to summarize content", extra={"error_msg": str(e)})
        doc["content"] = content[:5000]


async def _summarize_documents(documents: list[dict], llm_config: dict) -> None:
    if not documents:
        return
    model_name = llm_config["model_name"]
    client = client_for_config(llm_config)
    # Bounded concurrency so a 50-URL extract doesn't open 50 simultaneous LLM streams.
    sem = asyncio.Semaphore(4)

    async def _guarded(doc: dict) -> None:
        async with sem:
            await _summarize_doc(client, model_name, doc)

    await asyncio.gather(*(_guarded(d) for d in documents))


async def web_search_tool(query: str, limit: int = 5, user_settings: dict | None = None, **kwargs) -> str:
    user_settings = user_settings or {}

    selected = unquote_user_setting(user_settings.get("web.backend")) or _DEFAULT_BY_KIND["search"]
    provider = _get_provider(selected, user_settings, kind="search")
    # Search-only auto-fallback: when the user picked a key-requiring
    # provider but neither ``user_settings`` nor the deployment env has a
    # key, silently switch to the key-less default so search keeps working.
    # Only search does this — extract has no key-less backend. We don't
    # auto-fall back when the user already picked the default (e.g. ddgs
    # not installed); that path surfaces the explicit error below.
    if not provider.is_available() and provider.name != _DEFAULT_BY_KIND["search"]:
        logger.info(
            "Web search provider '%s' not configured; falling back to %s",
            provider.name,
            _DEFAULT_BY_KIND["search"],
        )
        provider = _get_provider(_DEFAULT_BY_KIND["search"], user_settings, kind="search")
    if not provider.is_available():
        return tool_error(f"{provider.display_name} is not configured or unavailable.")
    if not provider.supports_search():
        return tool_error(f"{provider.display_name} does not support search.")

    safe_limit = max(1, coerce_int(limit, 5))
    logger.info("Web search", extra={"provider_name": provider.name, "query": query, "limit": safe_limit})
    try:
        result = await provider.search(query, safe_limit)
    except Exception as e:
        return tool_error(f"Search error: {str(e)}")

    return json.dumps(result, ensure_ascii=False)


async def web_extract_tool(
    urls: list[str],
    llm_config: dict,
    format: str | None = None,
    use_llm_processing: bool = True,
    user_settings: dict | None = None,
    **kwargs,
) -> str:
    user_settings = user_settings or {}
    provider = resolve_extract_provider(user_settings)
    if not provider.is_available():
        msg = provider.missing_credential_message() or (f"{provider.display_name} is not configured or unavailable.")
        return tool_error(msg)
    if not provider.supports_extract():
        return tool_error(f"{provider.display_name} does not support extraction.")

    logger.info("Web extract", extra={"provider_name": provider.name, "url_count": len(urls)})
    try:
        documents = await provider.extract(urls)
    except Exception as e:
        return tool_error(f"Extraction error: {str(e)}")

    # Some providers return the legacy envelope {success, data: ...} — unwrap.
    if isinstance(documents, dict) and "data" in documents:
        documents = documents["data"]

    if use_llm_processing and isinstance(documents, list):
        # Fan out summarization across documents in parallel so a 10-URL extract
        # is bounded by the slowest single doc, not 10x latency.
        await _summarize_documents(documents, llm_config)

    return json.dumps({"success": True, "data": {"web": documents}}, ensure_ascii=False)


WEB_SEARCH_SCHEMA = {
    "name": "web_search",
    "description": 'Search the web for information. Returns up to 5 results by default with titles, URLs, and descriptions. The query is passed through to the configured backend, so operators such as site:domain, filetype:pdf, intitle:word, -term, and "exact phrase" may work when the backend supports them.',
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": 'The search query to look up on the web. You may include backend-supported operators such as site:example.com, filetype:pdf, intitle:word, -term, or "exact phrase".',
            },
            "limit": {"type": "integer", "description": "Maximum number of results to return. Defaults to 5.", "minimum": 1, "maximum": 100, "default": 5},
        },
        "required": ["query"],
    },
}

WEB_EXTRACT_SCHEMA = {
    "name": "web_extract",
    "description": "Extract content from web page URLs. Returns page content in markdown format. Also works with PDF URLs (arxiv papers, documents, etc.) — pass the PDF link directly and it converts to markdown text. Pages under 5000 chars return full markdown; larger pages are LLM-summarized and capped at ~5000 chars per page. Pages over 2M chars are refused. If a URL fails or times out, use the browser tool to access it instead.",
    "parameters": {
        "type": "object",
        "properties": {
            "urls": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of URLs to extract content from (max 5 URLs per call)",
            },
            "format": {"type": "string", "description": "Desired format (e.g. markdown)"},
            "use_llm_processing": {"type": "boolean", "description": "Summarize content with LLM (default: true)"},
        },
        "required": ["urls"],
    },
}


from ..registry import ALWAYS_AVAILABLE
from ..registry import REGISTRY
from ..registry import WEB_EXTRACT_AVAILABILITY

REGISTRY.register("web_search", WEB_SEARCH_SCHEMA, web_search_tool, ALWAYS_AVAILABLE)
REGISTRY.register("web_extract", WEB_EXTRACT_SCHEMA, web_extract_tool, WEB_EXTRACT_AVAILABILITY)
