import httpx

from ..base import ProviderError

# xAI's video endpoints return ``error: "<string>"`` on failure, not the
# OpenAI-style ``{"code", "message"}`` envelope the shared
# ``raise_for_provider_response`` helper assumes. The error parser lives here
# rather than inside ``video.py`` so the same convention used by
# ``minimax/_errors.py`` is followed — next provider that hits a non-OpenAI
# error shape should add a sibling module rather than inlining in the
# capability file.


def raise_for_grok_response(resp: httpx.Response, *, provider: str, model: str) -> dict:
    try:
        body = resp.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {"raw": str(body)} if body else {}
    if resp.status_code >= 400:
        err = body.get("error")
        msg = f"grok {provider} HTTP {resp.status_code}: {err if err else body}"
        raise ProviderError(msg, status_code=resp.status_code, body=body, provider=provider, model=model)
    return body
