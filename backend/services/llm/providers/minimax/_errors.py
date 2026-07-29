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
    1027: 400,  # content filter — classified by message keyword downstream
    1039: 429,  # concurrency / quota
    2013: 400,  # invalid param
}


def raise_for_minimax_response(resp, *, provider: str, model: str) -> dict:
    """Translate a MiniMax HTTP response into a dict body or raise
    :class:`ProviderError` with fields shaped for ``classify_api_error``.

    MiniMax wraps most error info in ``{"base_resp": {"status_code": N,
    "status_msg": "..."}, "data": null}`` — but the HTTP status is often
    still 200 even when ``base_resp.status_code != 0``. The HTTP-level
    4xx/5xx responses come back with their own JSON shape, so we handle both.
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
            http = _BASE_RESP_TO_HTTP.get(int(inner_code)) or resp.status_code or 400
            # 1027 is content-policy; mark so classify_api_error picks it up
            extra_body = {
                "error": {"code": str(inner_code), "message": inner_msg},
                "base_resp": base,
            }
            if int(inner_code) == 1027 and "content_filter" not in inner_msg:
                extra_body["error"]["message"] = f"{inner_msg} (content_filter)"
            raise ProviderError(
                f"minimax {provider} error {inner_code}: {inner_msg}",
                status_code=http,
                body=extra_body,
                provider=provider,
                model=model,
            )
        return body

    # Fall through to HTTP status
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
    import base64

    data = body.get("data") or {}
    audio_hex = data.get("audio") or ""
    if audio_hex:
        return bytes.fromhex(audio_hex)
    audio_b64 = data.get("audio_base64") or ""
    if audio_b64:
        return base64.b64decode(audio_b64)
    raise RuntimeError("MiniMax TTS response contained no audio payload")