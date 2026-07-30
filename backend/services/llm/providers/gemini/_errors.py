from collections.abc import Iterator

from ..base import ProviderError


def raise_for_gemini_response(resp, *, provider: str, model: str) -> dict:
    """Translate a Gemini HTTP response into a dict body or raise
    :class:`ProviderError` with fields shaped for ``classify_api_error``.

    Gemini returns error payloads as ``{"error": {"code": N, "message": ...,
    "status": "..."}}`` on both 4xx and 200-with-error; without surfacing it
    through ``ProviderError`` the retry loop can't route the call through
    the failover chain.
    """
    try:
        body = resp.json()
    except Exception:
        body = {}

    if isinstance(body, dict) and body.get("error"):
        err = body["error"]
        raise ProviderError(
            f"gemini {provider} error {err.get('code', '?')}: {err.get('message', '')}",
            status_code=resp.status_code,
            body=body,
            provider=provider,
            model=model,
        )
    if resp.status_code >= 400:
        raise ProviderError(
            f"gemini {provider} HTTP {resp.status_code}: {body}",
            status_code=resp.status_code,
            body=body if isinstance(body, dict) else {"raw": str(body)},
            provider=provider,
            model=model,
        )
    return body if isinstance(body, dict) else {}


def iter_parts(body: dict) -> Iterator[dict]:
    """Yield each ``content.parts[]`` entry across all candidates."""
    for candidate in body.get("candidates") or []:
        for part in (candidate.get("content") or {}).get("parts") or []:
            yield part
