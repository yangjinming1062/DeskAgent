import base64

from ..base import ProviderError

# MiniMax uses ``base_resp.status_code`` to carry its own error taxonomy on top
# of the HTTP status code.  Mappings below are tuned so that
# ``error_classifier.classify_api_error`` lands in the same FailoverReason
# buckets that an OpenAI-shaped error would for the same condition.
_BASE_RESP_TO_HTTP: dict[int, int] = {
    1002: 429,  # rate limit
    1004: 401,  # auth
    1008: 402,  # billing
    1013: 400,  # bad params
    1026: 400,  # content moderation — sensitive input, classified by message keyword downstream
    1027: 400,  # content filter — classified by message keyword downstream
    1039: 429,  # concurrency / quota
    2013: 400,  # invalid param
}

# MiniMax bills plan/credit refusals under its generic invalid-param codes, so
# the inner code alone can't distinguish them from a genuinely malformed request.
_ENTITLEMENT_SIGNALS = ("tokenplan", "credit")


def raise_for_minimax_response(resp, *, provider: str, model: str) -> dict:
    """Translate a MiniMax HTTP response into a dict body or raise
    :class:`ProviderError` with fields shaped for ``classify_api_error``.

    MiniMax wraps most error info in ``{"base_resp": {"status_code": N,
    "status_msg": "..."}, "data": null}`` — but the HTTP status is often
    still 200 even when ``base_resp.status_code != 0``. The HTTP-level
    4xx/5xx responses come back with their own JSON shape, so we handle both.

    Known inner codes land in ``_BASE_RESP_TO_HTTP``. Unknown inner codes
    map to 502 (provider upstream failure) instead of falling back to
    ``resp.status_code`` — the latter is often 200, which would tell
    error_classifier the call succeeded.
    """
    try:
        body = resp.json()
    except Exception:
        body = {}

    if isinstance(body, dict) and "base_resp" in body:
        base = body.get("base_resp") or {}
        inner_code = base.get("status_code", 0)
        inner_msg = base.get("status_msg", "") or ""
        if inner_code and inner_code != 0:
            try:
                inner_int = int(inner_code)
            except (TypeError, ValueError):
                inner_int = 0
            if inner_int in (1013, 2013) and any(s in inner_msg.lower() for s in _ENTITLEMENT_SIGNALS):
                http = 402
            elif inner_int in _BASE_RESP_TO_HTTP:
                http = _BASE_RESP_TO_HTTP[inner_int]
            else:
                # Don't fall back to resp.status_code (likely 200); surface
                # as a generic 502 so error_classifier marks it retryable.
                http = 502
            extra_body = {"error": {"code": str(inner_code), "message": inner_msg}, "base_resp": base}
            # Don't pollute the message with internal marker words — the
            # classifier uses the message TEXT to detect content-filter;
            # using ``content_filter`` as a sentinel here makes sense only
            # when the upstream actually mentioned it.
            raise ProviderError(f"minimax {provider} error {inner_code}: {inner_msg}", status_code=http, body=extra_body, provider=provider, model=model)
        return body

    if resp.status_code >= 400:
        raise ProviderError(
            f"minimax {provider} HTTP {resp.status_code}: {body}",
            status_code=resp.status_code,
            body=body if isinstance(body, dict) else {"raw": str(body)},
            provider=provider,
            model=model,
        )
    return body if isinstance(body, dict) else {}


def extract_minimax_audio(body: dict) -> bytes:
    """MiniMax TTS returns audio as a hex-encoded string under data.audio."""
    data = body.get("data") or {}
    audio_hex = data.get("audio") or ""
    if audio_hex:
        return bytes.fromhex(audio_hex)
    audio_b64 = data.get("audio_base64") or ""
    if audio_b64:
        return base64.b64decode(audio_b64)
    raise RuntimeError("MiniMax TTS response contained no audio payload")
