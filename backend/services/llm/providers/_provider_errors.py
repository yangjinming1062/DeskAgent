from .base import ProviderError


def raise_for_provider_response(resp, *, family: str, model: str) -> dict:
    """Translate an HTTP response into a dict body or raise :class:`ProviderError`.

    Providers that wrap errors as ``{"error": {"code": ..., "message": ...}}``
    on 4xx/5xx (or on 200-with-error-envelope) route through here so the retry
    loop can classify the failure and walk the failover chain. ``family`` is
    the provider label used in the message and ``ProviderError.provider``.
    """
    try:
        body = resp.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {"raw": str(body)} if body else {}

    if err := body.get("error"):
        msg = f"{family} error {err.get('code', '?')}: {err.get('message', '')}"
    elif resp.status_code >= 400:
        msg = f"{family} HTTP {resp.status_code}: {body}"
    else:
        return body

    raise ProviderError(msg, status_code=resp.status_code, body=body, provider=family, model=model)