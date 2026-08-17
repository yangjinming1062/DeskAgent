from .base import ProviderError


def _format_err(family: str, err) -> str:
    """Render a provider error payload into a single string.

    Tolerates the four observed shapes:
      * ``{"code": "...", "message": "..."}`` — full envelope (most providers)
      * ``"plain string"`` — Grok /images/edits when the upstream returns
        ``{"error": "<string>"}`` (rarely documented but seen in the wild)
      * ``None`` / falsy — empty envelope
      * arbitrary nested objects with a stringifiable ``message`` field
    """
    if not err:
        return f"{family} error (no detail)"
    if isinstance(err, str):
        return f"{family} error: {err}" if err else f"{family} error (empty)"
    if isinstance(err, dict):
        code = err.get("code", "?")
        msg = err.get("message", err.get("detail", ""))
        return f"{family} error {code}: {msg}" if msg else f"{family} error {code}"
    return f"{family} error: {err!r}"


def raise_for_provider_response(resp, *, family: str, model: str) -> dict:
    """Translate an HTTP response into a dict body or raise :class:`ProviderError`.

    Providers that wrap errors as ``{"error": {"code": ..., "message": ...}}``
    on 4xx/5xx (or on 200-with-error-envelope) route through here so the retry
    loop can classify the failure and walk the failover chain. ``family`` is
    the provider label used in the message and ``ProviderError.provider``.

    The error payload's ``error`` field can be a dict (typical OpenAI / Gemini
    shape), a plain string (Grok /images/edits observed), ``None``, or any
    other nested form — ``_format_err`` normalizes all of them so this helper
    never blows up on a weird provider payload.
    """
    try:
        body = resp.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {"raw": str(body)} if body else {}

    if err := body.get("error"):
        msg = _format_err(family, err)
    elif resp.status_code >= 400:
        msg = f"{family} HTTP {resp.status_code}: {body}"
    else:
        return body

    raise ProviderError(msg, status_code=resp.status_code, body=body, provider=family, model=model)
