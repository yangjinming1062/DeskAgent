import enum
import re
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field
from typing import Any

from components import get_logger
from components import redact_sensitive_text
from components import safe_json_loads

logger = get_logger(__name__)


# ── Error taxonomy ──────────────────────────────────────────────────────


class FailoverReason(enum.Enum):
    """Why an API call failed — determines recovery strategy."""

    # Authentication / authorization
    auth = "auth"  # Transient auth (401/403) — refresh/rotate

    # Billing / quota
    billing = "billing"  # 402 or confirmed credit exhaustion — rotate immediately
    rate_limit = "rate_limit"  # 429 or quota-based throttling — backoff then rotate

    # Server-side
    overloaded = "overloaded"  # 503/529 — provider overloaded, backoff
    server_error = "server_error"  # 500/502 — internal server error, retry

    # Transport
    timeout = "timeout"  # Connection/read timeout — rebuild client + retry

    # Context / payload
    context_overflow = "context_overflow"  # Context too large — compress, not failover
    payload_too_large = "payload_too_large"  # 413 — compress payload
    image_too_large = "image_too_large"  # Native image part exceeds provider's per-image limit — shrink and retry

    # Model / provider policy
    model_not_found = "model_not_found"  # 404 or invalid model — fallback to different model
    provider_policy_blocked = "provider_policy_blocked"  # Aggregator (e.g. OpenRouter) blocked the only endpoint due to account data/privacy policy
    content_policy_blocked = "content_policy_blocked"  # Provider safety filter rejected this prompt — deterministic per-request, don't retry unchanged

    # Request format
    format_error = "format_error"  # 400 bad request — abort or strip + retry
    invalid_encrypted_content = "invalid_encrypted_content"  # Responses replay blob rejected — strip replay state and retry
    multimodal_tool_content_unsupported = (
        "multimodal_tool_content_unsupported"  # Provider rejected list-type content in tool messages (e.g. Xiaomi MiMo) — downgrade to text and retry
    )
    attachment_fetch_failed = (
        "attachment_fetch_failed"  # Provider couldn't fetch a URL from an image_url part. The backend has nothing else to try on retry; surface a curated user-facing message.
    )

    # Provider-specific
    thinking_signature = "thinking_signature"  # Anthropic thinking block sig invalid
    long_context_tier = "long_context_tier"  # Anthropic "extra usage" tier gate
    oauth_long_context_beta_forbidden = "oauth_long_context_beta_forbidden"  # Anthropic OAuth subscription rejects 1M context beta — disable beta and retry
    llama_cpp_grammar_pattern = "llama_cpp_grammar_pattern"  # llama.cpp json-schema-to-grammar rejects regex escapes in `pattern` / `format` — strip from tools and retry

    # Catch-all
    unknown = "unknown"  # Unclassifiable — retry with backoff


# ── Classification result ───────────────────────────────────────────────


@dataclass
class ClassifiedError:
    """Structured classification of an API error with recovery hints."""

    reason: FailoverReason
    status_code: int | None = None
    provider: str | None = None
    model: str | None = None
    message: str = ""
    error_context: dict[str, Any] = field(default_factory=dict)

    # Recovery action hints — the retry loop checks these instead of
    # re-classifying the error itself.
    retryable: bool = True
    should_compress: bool = False
    should_rotate_credential: bool = False
    should_fallback: bool = False
    suggested_delay: float | None = None


_ClassifierBuilder = Callable[..., ClassifiedError]


# ── Provider-specific patterns ──────────────────────────────────────────

# Patterns that indicate billing exhaustion (not transient rate limit)
_BILLING_PATTERNS = [
    "insufficient credits",
    "insufficient_quota",
    "insufficient balance",
    "credit balance",
    "credits exhausted",
    "credits have been exhausted",
    "no usable credits",
    "top up your credits",
    "payment required",
    "billing hard limit",
    "exceeded your current quota",
    "account is deactivated",
    "plan does not include",
    "out of funds",
    "run out of funds",
    "balance_depleted",
    "model_not_supported_on_free_tier",
    "not available on the free tier",
    "余额不足",
    "额度不足",
    "配额不足",
]

# Patterns that indicate rate limiting (transient, will resolve)
_RATE_LIMIT_PATTERNS = [
    "rate limit",
    "rate_limit",
    "too many requests",
    "throttled",
    "requests per minute",
    "tokens per minute",
    "requests per day",
    "try again in",
    "please retry after",
    "resource_exhausted",
    "rate increased too quickly",  # Alibaba/DashScope throttling
    # AWS Bedrock throttling
    "throttlingexception",
    "too many concurrent requests",
    "servicequotaexceededexception",
]

# Provider/aggregator couldn't fetch an image URL embedded in an image_url
# part. Litellm SigV4-presigns gs:// into https://storage.googleapis.com and
# GCS returns 400 when HMAC mismatches; some proxies emit the generic "unable
# to fetch" wording. Match before the format_error fall-through so the user
# sees a curated reason instead of a misleading 400 tail.
_ATTACHMENT_FETCH_PATTERNS = [
    "unable to fetch image from url",
    "unable to fetch the image",
    "could not fetch image",
    "error fetching image",
    "failed to download image from url",
]

# Usage-limit patterns that need disambiguation (could be billing OR rate_limit)
_USAGE_LIMIT_PATTERNS = [
    "usage limit",
    "quota",
    "limit exceeded",
    "key limit exceeded",
]

# Patterns confirming usage limit is transient (not billing)
_USAGE_LIMIT_TRANSIENT_SIGNALS = [
    "try again",
    "retry",
    "resets at",
    "reset in",
    "wait",
    "requests remaining",
    "periodic",
    "window",
]

# Payload-too-large patterns detected from message text (no status_code attr).
# Proxies and some backends embed the HTTP status in the error message.
_PAYLOAD_TOO_LARGE_PATTERNS = [
    "request entity too large",
    "payload too large",
    "error code: 413",
]

# Image-size patterns.  Matched against 400 bodies (not 413) because most
# providers return a 400 with a specific image-too-big message before the
# whole request hits the 413 size limit.  Anthropic's wording is the most
# important here (hard 5 MB per image, returned as
# "messages.N.content.K.image.source.base64: image exceeds 5 MB maximum").
_IMAGE_TOO_LARGE_PATTERNS = [
    "image exceeds",  # Anthropic: "image exceeds 5 MB maximum"
    "image too large",  # generic
    "image_too_large",  # error_code variant
    "image size exceeds",  # variant
    "image dimensions exceed",  # Anthropic: "image dimensions exceed max allowed size: 8000 pixels"
    "dimensions exceed max allowed size",  # Anthropic dimension-cap (wording variant)
    "max allowed size: 8000",  # Anthropic dimension-cap (explicit pixel ceiling)
    # "request_too_large" on a request known to contain an image → image is
    # the likely culprit; we still try the shrink path before giving up.
]

# Providers that follow the OpenAI spec strictly require tool message
# ``content`` to be a string.  Some (Anthropic native, Codex Responses,
# Gemini native, first-party OpenAI) extend this to accept a content-parts
# list (text + image_url) so multimodal tool results survive.  Others
# (Xiaomi MiMo, some Alibaba endpoints, a long tail of OpenAI-compatible
# providers) reject the list with a 400 — the patterns below are the most
# common error shapes we see.  Recovery: strip image parts from tool
# messages in-place, record the (provider, model) for the rest of the
# session so we don't waste another call learning the same lesson, retry.
#
# See: https://github.com/NousResearch/desk-agent/issues/27344
_MULTIMODAL_TOOL_CONTENT_PATTERNS = [
    # Xiaomi MiMo: {"error":{"code":"400","message":"Param Incorrect","param":"text is not set"}}
    "text is not set",
    # Generic "tool message must be string" shapes
    "tool message content must be a string",
    "tool content must be a string",
    "tool message must be a string",
    # OpenAI-compat servers that reject list-type tool content with a
    # schema-validation message
    "expected string, got list",
    "expected string, got array",
    # Alibaba/DashScope variant
    "tool_call.content must be string",
]

# Context overflow patterns
_CONTEXT_OVERFLOW_PATTERNS = [
    "context length",
    "context size",
    "maximum context",
    "token limit",
    "too many tokens",
    "reduce the length",
    "exceeds the limit",
    "context window",
    "prompt is too long",
    "prompt exceeds max length",
    "max_tokens",
    "maximum number of tokens",
    # vLLM / local inference server patterns
    "exceeds the max_model_len",
    "max_model_len",
    "prompt length",  # "engine prompt length X exceeds"
    "input is too long",
    "maximum model length",
    # Ollama patterns
    "context length exceeded",
    "truncating input",
    # llama.cpp / llama-server patterns
    "slot context",  # "slot context: N tokens, prompt N tokens"
    "n_ctx_slot",
    # Chinese error messages (some providers return these)
    "超过最大长度",
    "上下文长度",
    # AWS Bedrock Converse API error patterns
    "input is too long",
    "max input token",
    "input token",
    "exceeds the maximum number of input tokens",
]

# Model not found patterns
_MODEL_NOT_FOUND_PATTERNS = [
    "is not a valid model",
    "invalid model",
    "model not found",
    "model_not_found",
    "does not exist",
    "no such model",
    "unknown model",
    "unsupported model",
]

# Request-validation patterns — the request is malformed and will fail
# identically on every retry. Some OpenAI-compatible gateways (notably
# codex.nekos.me) return these as 5xx instead of the standard 4xx, which
# makes the generic "5xx → retryable server_error" rule misfire: the retry
# loop hammers the same deterministic rejection 3+ times, then the
# transport-recovery path resets the counter and does it again, producing
# a request flood. When a 5xx body carries one of these unambiguous
# request-validation signals, classify as a non-retryable format_error so
# the loop fails fast and falls back instead of looping.
_REQUEST_VALIDATION_PATTERNS = [
    "unknown parameter",
    "unsupported parameter",
    "unrecognized request argument",
    "invalid_request_error",
    "unknown_parameter",
    "unsupported_parameter",
]

_REQUEST_VALIDATION_ERROR_CODES = frozenset({"invalid_request_error", "unknown_parameter", "unsupported_parameter"})

# OpenRouter aggregator policy-block patterns.
#
# When a user's OpenRouter account privacy setting (or a per-request
# `provider.data_collection: deny` preference) excludes the only endpoint
# serving a model, OpenRouter returns 404 with a *specific* message that is
# distinct from "model not found":
#
#   "No endpoints available matching your guardrail restrictions and
#    data policy. Configure: https://openrouter.ai/settings/privacy"
#
# We classify this as `provider_policy_blocked` rather than
# `model_not_found` because:
#   - The model *exists* — model_not_found is misleading in logs
#   - Provider fallback won't help: the account-level setting applies to
#     every call on the same OpenRouter account
#   - The error body already contains the fix URL, so the user gets
#     actionable guidance without us rewriting the message
_PROVIDER_POLICY_BLOCKED_PATTERNS = [
    "no endpoints available matching your guardrail",
    "no endpoints available matching your data policy",
    "no endpoints found matching your data policy",
]

# Provider content-policy / safety-filter blocks. Distinct from
# ``provider_policy_blocked`` above (which is an OpenRouter *account*-level
# data/privacy guardrail) — these are *per-prompt* safety decisions made by
# the upstream model provider. They are deterministic for the unchanged
# request, so retrying the same prompt three times just reproduces the same
# block and burns paid attempts on a refusal. The recovery is to switch to a
# configured fallback model/provider immediately, or surface the block to
# the user with actionable guidance if no fallback exists.
#
# Patterns are intentionally narrow — each phrase is a verbatim string from
# a specific provider's safety pipeline, not a generic word like "policy" or
# "violation" that could collide with billing/auth/format errors:
#   • OpenAI Codex cybersecurity refusal (gpt-5.5, the case from #18028)
#   • OpenAI moderation refusal ("violates our usage policies", with
#     "usage policies" disambiguating from billing's "exceeded ... policy")
#   • Anthropic safety refusal ("prompt was flagged by ... safety system")
#   • OpenAI Responses content filter
#   • MiniMax safety refusal ("violated safety policy" — base_resp 1027)
_CONTENT_POLICY_BLOCKED_PATTERNS = [
    # OpenAI Codex (#18028) — message may arrive without an HTTP status
    "flagged for possible cybersecurity risk",
    "trusted access for cyber",
    # OpenAI moderation — chat completions / responses
    "violates our usage policies",
    "violates openai's usage policies",
    "your request was flagged by",
    # Anthropic safety system
    "prompt was flagged by our safety",
    "responses cannot be generated due to safety",
    # MiniMax (base_resp.status_code 1027) — verbatim message seen in their
    # safety pipeline. ``violated safety policy`` is enough to distinguish
    # from billing's "exceeded ... policy" without false positives.
    "violated safety policy",
    # MiniMax (base_resp.status_code 1026) — sensitive input moderation.
    # ``new_sensitive`` is the verbatim status_msg token for this code.
    "new_sensitive",
    # Generic content-filter wording seen on Azure / OpenAI Responses.
    # ``content_filter`` (underscore) is the OpenAI-standard error/finish
    # token surfaced verbatim by their SDKs when a request is blocked.
    # ``responsibleaipolicyviolation`` is Azure OpenAI's error code.
    # Deliberately NOT matching the space variant ("content filter") — it
    # appears in benign config descriptions and tooltip text that providers
    # echo back; the underscore form is provider-specific enough.
    "content_filter",
    "responsibleaipolicyviolation",
]


def is_content_policy_error_message(msg: str) -> bool:
    """Checks the same pattern list as ``classify_api_error`` but on a raw string."""
    return any(p in msg.lower() for p in _CONTENT_POLICY_BLOCKED_PATTERNS)


# Auth patterns (non-status-code signals)
_AUTH_PATTERNS = [
    "invalid api key",
    "invalid_api_key",
    "authentication",
    "unauthorized",
    "forbidden",
    "invalid token",
    "token expired",
    "token revoked",
    "access denied",
]

# Message-string patterns that indicate a provider-side timeout even when
# the exception type is generic (e.g. RuntimeError from a local shim that
# wraps a subprocess timeout).  Checked before the type-based transport
# heuristics so custom-provider "timed out" errors don't fall through to
# the unknown bucket and get misreported as empty responses.
_TIMEOUT_MESSAGE_PATTERNS = [
    "timed out",
    "turn timed out",
    "request timed out",
    "deadline exceeded",
    "operation timed out",
    "upstream timed out",
]

# Transport error type names
_TRANSPORT_ERROR_TYPES = frozenset(
    {
        "ReadTimeout",
        "ConnectTimeout",
        "PoolTimeout",
        "ConnectError",
        "RemoteProtocolError",
        "ConnectionError",
        "ConnectionResetError",
        "ConnectionAbortedError",
        "BrokenPipeError",
        "TimeoutError",
        "ReadError",
        "ServerDisconnectedError",
        # SSL/TLS transport errors — transient mid-stream handshake/record
        # failures that should retry rather than surface as a stalled session.
        # ssl.SSLError subclasses OSError (caught by isinstance) but we list
        # the type names here so provider-wrapped SSL errors (e.g. when the
        # SDK re-raises without preserving the exception chain) still classify
        # as transport rather than falling through to the unknown bucket.
        "SSLError",
        "SSLZeroReturnError",
        "SSLWantReadError",
        "SSLWantWriteError",
        "SSLEOFError",
        "SSLSyscallError",
        # OpenAI SDK errors (not subclasses of Python builtins)
        "APIConnectionError",
        "APITimeoutError",
    }
)

# Server disconnect patterns (no status code, but transport-level).
# These are the "ambiguous" patterns — a plain connection close could be
# transient transport hiccup OR server-side context overflow rejection
# (common when the API gateway disconnects instead of returning an HTTP
# error for oversized requests).  A large session + one of these patterns
# triggers the context-overflow-with-compression recovery path.
_SERVER_DISCONNECT_PATTERNS = [
    "server disconnected",
    "peer closed connection",
    "connection reset by peer",
    "connection was closed",
    "network connection lost",
    "unexpected eof",
    "incomplete chunked read",
]

# SSL/TLS transient failure patterns — intentionally distinct from
# _SERVER_DISCONNECT_PATTERNS above.
#
# An SSL alert mid-stream is almost always a transport-layer hiccup
# (flaky network, mid-session TLS renegotiation failure, load balancer
# dropping the connection) — NOT a server-side context overflow signal.
# So we want the retry path but NOT the compression path; lumping these
# into _SERVER_DISCONNECT_PATTERNS would trigger unnecessary (and
# expensive) context compression on any large-session SSL hiccup.
#
# The OpenSSL library constructs error codes by prepending a format string
# to the uppercased alert reason; OpenSSL 3.x changed the separator
# (e.g. `SSLV3_ALERT_BAD_RECORD_MAC` → `SSL/TLS_ALERT_BAD_RECORD_MAC`),
# which silently stopped matching anything explicit.  Matching on the
# stable substrings (`bad record mac`, `ssl alert`, `tls alert`, etc.)
# survives future OpenSSL format churn without code changes.
_SSL_TRANSIENT_PATTERNS = [
    # Space-separated (human-readable form, Python ssl module, most SDKs)
    "bad record mac",
    "ssl alert",
    "tls alert",
    "ssl handshake failure",
    "tlsv1 alert",
    "sslv3 alert",
    # Underscore-separated (OpenSSL error code tokens, e.g.
    # `ERR_SSL_SSL/TLS_ALERT_BAD_RECORD_MAC`, `SSLV3_ALERT_BAD_RECORD_MAC`)
    "bad_record_mac",
    "ssl_alert",
    "tls_alert",
    "tls_alert_internal_error",
    # Python ssl module prefix, e.g. "[SSL: BAD_RECORD_MAC]"
    "[ssl:",
]

_BILLING_ERROR_CODES = frozenset(
    {
        "insufficient_quota",
        "billing_not_active",
        "payment_required",
        "insufficient_credits",
        "no_usable_credits",
        "balance_depleted",
        "model_not_supported_on_free_tier",
    }
)

_RATE_LIMIT_ERROR_CODES = frozenset(
    {
        "resource_exhausted",
        "throttled",
        "rate_limit_exceeded",
    }
)

_MODEL_NOT_FOUND_ERROR_CODES = frozenset(
    {
        "model_not_found",
        "model_not_available",
        "invalid_model",
    }
)

_CONTEXT_OVERFLOW_ERROR_CODES = frozenset(
    {
        "context_length_exceeded",
        "max_tokens_exceeded",
    }
)


# ── Classification pipeline ─────────────────────────────────────────────


def classify_api_error(
    error: Exception,
    *,
    provider: str = "",
    model: str = "",
    approx_tokens: int = 0,
    context_length: int = 200000,
    num_messages: int = 0,
) -> ClassifiedError:
    """Classify an API error into a structured recovery recommendation.

    Priority-ordered pipeline:
      1. Special-case provider-specific patterns (thinking sigs, tier gates)
      2. HTTP status code + message-aware refinement
      3. Error code classification (from body)
      4. Message pattern matching (billing vs rate_limit vs context vs auth)
      5. SSL/TLS transient alert patterns → retry as timeout
      6. Server disconnect + large session → context overflow
      7. Transport error heuristics
      8. Fallback: unknown (retryable with backoff)

    Args:
        error: The exception from the API call.
        provider: Current provider name (e.g. "openrouter", "anthropic").
        model: Current model slug.
        approx_tokens: Approximate token count of the current context.
        context_length: Maximum context length for the current model.

    Returns:
        ClassifiedError with reason and recovery action hints.
    """
    status_code = _extract_status_code(error)
    suggested_delay = _extract_suggested_delay(error)
    error_type = type(error).__name__
    # Copilot/GitHub Models RateLimitError may not set .status_code; force 429
    # so downstream rate-limit handling (classifier reason, pool rotation,
    # fallback gating) fires correctly instead of misclassifying as generic.
    if status_code is None and error_type == "RateLimitError":
        status_code = 429
    body = _extract_error_body(error)
    error_code = _extract_error_code(body)

    # Build a comprehensive error message string for pattern matching.
    # str(error) alone may not include the body message (e.g. OpenAI SDK's
    # APIStatusError.__str__ returns the first arg, not the body).  Append
    # the body message so patterns like "try again" in 402 disambiguation
    # are detected even when only present in the structured body.
    #
    # Also extract metadata.raw — OpenRouter wraps upstream provider errors
    # inside {"error": {"message": "Provider returned error", "metadata":
    # {"raw": "<actual error JSON>"}}} and the real error message (e.g.
    # "context length exceeded") is only in the inner JSON.
    error_msg = _build_error_message(error, body)
    provider_lower = (provider or "").strip().lower()
    model_lower = (model or "").strip().lower()

    def _result(reason: FailoverReason, **overrides) -> ClassifiedError:
        defaults = {
            "reason": reason,
            "status_code": status_code,
            "provider": provider,
            "model": model,
            "message": _extract_message(error, body),
            "suggested_delay": suggested_delay,
        }
        defaults.update(overrides)
        return ClassifiedError(**defaults)

    # ── 1. Provider-specific patterns (highest priority) ────────────

    if special := _classify_provider_specific(error_msg, status_code, _result):
        return special

    # ── 2. HTTP status code classification ──────────────────────────

    if status_code is not None:
        classified = _classify_by_status(
            status_code,
            error_msg,
            error_code,
            body,
            provider=provider_lower,
            model=model_lower,
            approx_tokens=approx_tokens,
            context_length=context_length,
            num_messages=num_messages,
            result_fn=_result,
        )
        if classified is not None:
            return classified

    # ── 3. Error code classification ────────────────────────────────

    if error_code:
        classified = _classify_by_error_code(error_code, error_msg, _result)
        if classified is not None:
            return classified

    # ── 4. Message pattern matching (no status code) ────────────────

    classified = _classify_by_message(
        error_msg,
        error_type,
        approx_tokens=approx_tokens,
        context_length=context_length,
        result_fn=_result,
    )
    if classified is not None:
        return classified

    # ── 5. SSL/TLS transient errors → retry as timeout (not compression) ──
    # SSL alerts mid-stream are transport hiccups, not server-side context
    # overflow signals.  Classify before the disconnect check so a large
    # session doesn't incorrectly trigger context compression when the real
    # cause is a flaky TLS handshake.  Also matches when the error is
    # wrapped in a generic exception whose message string carries the SSL
    # alert text but the type isn't ssl.SSLError (happens with some SDKs
    # that re-raise without chaining).
    if any(p in error_msg for p in _SSL_TRANSIENT_PATTERNS):
        return _result(FailoverReason.timeout, retryable=True)

    # ── 6. Server disconnect + large session → context overflow ─────
    # Must come BEFORE generic transport error catch — a disconnect on
    # a large session is more likely context overflow than a transient
    # transport hiccup.  Without this ordering, RemoteProtocolError
    # always maps to timeout regardless of session size.

    if any(p in error_msg for p in _SERVER_DISCONNECT_PATTERNS) and not status_code:
        # Absolute token/message-count thresholds are only a proxy for smaller
        # context windows.  Large-context sessions can have hundreds of
        # messages while still being far below their actual token budget.
        is_large = approx_tokens > context_length * 0.6 or (context_length <= 256000 and (approx_tokens > 120000 or num_messages > 200))
        if is_large:
            return _result(
                FailoverReason.context_overflow,
                retryable=True,
                should_compress=True,
            )
        return _result(FailoverReason.timeout, retryable=True)

    # ── 7. Transport / timeout heuristics ───────────────────────────

    if error_type in _TRANSPORT_ERROR_TYPES or isinstance(error, (TimeoutError, ConnectionError, OSError)):
        return _result(FailoverReason.timeout, retryable=True)

    # ── 8. Fallback: unknown ────────────────────────────────────────

    return _result(FailoverReason.unknown, retryable=True)


def _classify_provider_specific(error_msg: str, status_code: int | None, result_fn: _ClassifierBuilder) -> ClassifiedError | None:
    """Classify provider-specific patterns that take priority over status codes."""
    # Provider content-policy / safety-filter block. The provider has made a
    # deterministic refusal decision about THIS prompt — retrying unchanged
    # just reproduces the same refusal and burns paid attempts. Must run
    # before status-based classification so a 400 safety block isn't
    # downgraded to a generic ``format_error`` and a status-less block
    # (OpenAI Codex SDK can raise without one) isn't left in the retryable
    # ``unknown`` bucket. See issue #18028.
    if any(p in error_msg for p in _CONTENT_POLICY_BLOCKED_PATTERNS):
        return result_fn(
            FailoverReason.content_policy_blocked,
            retryable=False,
            should_fallback=True,
        )

    # Anthropic thinking block signature invalid (400).
    # Don't gate on provider — OpenRouter proxies Anthropic errors, so the
    # provider may be "openrouter" even though the error is Anthropic-specific.
    # The message pattern ("signature" + "thinking") is unique enough.
    if status_code == 400 and "signature" in error_msg and "thinking" in error_msg:
        return result_fn(
            FailoverReason.thinking_signature,
            retryable=True,
            should_compress=False,
        )

    # Anthropic long-context tier gate (429 "extra usage" + "long context")
    if status_code == 429 and "extra usage" in error_msg and "long context" in error_msg:
        return result_fn(
            FailoverReason.long_context_tier,
            retryable=True,
            should_compress=True,
        )

    # Anthropic OAuth subscription rejects the 1M-context beta header.
    # Observed error body: "The long context beta is not yet available for
    # this subscription." Returned as HTTP 400 from native Anthropic when
    # the subscription doesn't include 1M context, even though the request
    # carries ``anthropic-beta: context-1m-2025-08-07``. The recovery path
    # in run_agent.py rebuilds the Anthropic client with the beta stripped
    # and retries once. Pattern is narrow enough that it won't collide with
    # the 429 tier-gate pattern above (different status, different phrase).
    if status_code == 400 and "long context beta" in error_msg and "not yet available" in error_msg:
        return result_fn(
            FailoverReason.oauth_long_context_beta_forbidden,
            retryable=True,
            should_compress=False,
        )

    # llama.cpp's ``json-schema-to-grammar`` converter (used by its OAI
    # server to build GBNF tool-call parsers) rejects regex escape classes
    # like ``\d``/``\w``/``\s`` and most ``format`` values. MCP servers
    # routinely emit ``"pattern": "\\d{4}-\\d{2}-\\d{2}"`` for date/phone/
    # email params. llama.cpp surfaces this as HTTP 400 with one of a few
    # recognizable phrases; on match we strip ``pattern``/``format`` from
    # ``self.tools`` in the retry loop and retry once. Cloud providers are
    # unaffected — they accept these keywords and we never hit this branch.
    if status_code == 400 and (
        "error parsing grammar" in error_msg or "json-schema-to-grammar" in error_msg or ("unable to generate parser" in error_msg and "template" in error_msg)
    ):
        return result_fn(
            FailoverReason.llama_cpp_grammar_pattern,
            retryable=True,
            should_compress=False,
        )

    # xAI Grok subscription entitlement errors.
    #
    # xAI returns "You have either run out of available resources or do not
    # have an active Grok subscription" through two distinct code paths:
    #
    #   • HTTP 403 — status_code is set; _classify_by_status (step 2) routes
    #     it to FailoverReason.auth correctly, and _is_entitlement_failure
    #     then prevents the credential-refresh loop.
    #
    #   • SSE ``type=error`` frame — surfaced as _StreamErrorEvent with
    #     status_code=None.  _classify_by_status is skipped entirely, and
    #     "grok subscription" / "out of available resources" appear in none
    #     of the message-pattern lists below.  Without this guard the error
    #     falls through to FailoverReason.unknown (retryable=True), burning
    #     max_retries before the agent stops — and _is_entitlement_failure
    #     is never called because it only runs under FailoverReason.auth.
    #
    # Both X Premium+ and SuperGrok subscribers hit this path when their
    # subscription tier does not cover the requested model or feature.
    if "do not have an active grok subscription" in error_msg or ("out of available resources" in error_msg and "grok" in error_msg):
        return result_fn(
            FailoverReason.auth,
            retryable=False,
            should_fallback=True,
        )

    return None


# ── Status code classification ──────────────────────────────────────────


def _classify_by_status(
    status_code: int,
    error_msg: str,
    error_code: str,
    body: dict,
    *,
    provider: str,
    model: str,
    approx_tokens: int,
    context_length: int,
    num_messages: int = 0,
    result_fn: _ClassifierBuilder,
) -> ClassifiedError | None:
    """Classify based on HTTP status code with message-aware refinement."""
    match status_code:
        case 401:
            return result_fn(
                FailoverReason.auth,
                retryable=False,
                should_rotate_credential=True,
                should_fallback=True,
            )

        case 403:
            if "key limit exceeded" in error_msg or "spending limit" in error_msg or any(p in error_msg for p in _BILLING_PATTERNS):
                return result_fn(
                    FailoverReason.billing,
                    retryable=False,
                    should_rotate_credential=True,
                    should_fallback=True,
                )
            return result_fn(
                FailoverReason.auth,
                retryable=False,
                should_fallback=True,
            )

        case 402:
            return _classify_402(error_msg, result_fn)

        case 404:
            return _classify_404(error_msg, result_fn)

        case 413:
            return result_fn(
                FailoverReason.payload_too_large,
                retryable=True,
                should_compress=True,
            )

        case 429:
            return result_fn(
                FailoverReason.rate_limit,
                retryable=True,
                should_rotate_credential=True,
                should_fallback=True,
            )

        case 400:
            return _classify_400(
                error_msg,
                error_code,
                body,
                provider=provider,
                model=model,
                approx_tokens=approx_tokens,
                context_length=context_length,
                num_messages=num_messages,
                result_fn=result_fn,
            )

        case 500 | 502:
            # Some OpenAI-compatible gateways return request-validation errors
            # with a 5xx status (codex.nekos.me returns 502 for unknown/
            # unsupported parameters). These are deterministic — every retry
            # gets the identical rejection — so the generic "5xx → retryable
            # server_error" rule turns one bad request into a retry flood.
            if any(p in error_msg for p in _REQUEST_VALIDATION_PATTERNS) or error_code.lower() in _REQUEST_VALIDATION_ERROR_CODES:
                return result_fn(
                    FailoverReason.format_error,
                    retryable=False,
                    should_fallback=True,
                )
            return result_fn(FailoverReason.server_error, retryable=True)

        case 503 | 529:
            return result_fn(FailoverReason.overloaded, retryable=True)

        case s if 400 <= s < 500:
            return result_fn(
                FailoverReason.format_error,
                retryable=False,
                should_fallback=True,
            )

        case s if 500 <= s < 600:
            return result_fn(FailoverReason.server_error, retryable=True)

        case _:
            return None


def _classify_404(error_msg: str, result_fn: _ClassifierBuilder) -> ClassifiedError:
    """Classify 404 — billing, policy-block, model-not-found, or unknown."""
    # Nous API currently surfaces HA/NAS credit depletion as a paid model
    # becoming unavailable on the Free Tier, returned as 404 rather than
    # 402. Treat that as entitlement/billing exhaustion, not a missing
    # model, so the retry loop can show credit/top-up guidance.
    if any(p in error_msg for p in _BILLING_PATTERNS):
        return result_fn(
            FailoverReason.billing,
            retryable=False,
            should_rotate_credential=True,
            should_fallback=True,
        )
    # OpenRouter policy-block 404 — distinct from "model not found".
    # The model exists; the user's account privacy setting excludes the
    # only endpoint serving it. Falling back to another provider won't
    # help (same account setting applies).  The error body already
    # contains the fix URL, so just surface it.
    if any(p in error_msg for p in _PROVIDER_POLICY_BLOCKED_PATTERNS):
        return result_fn(
            FailoverReason.provider_policy_blocked,
            retryable=False,
            should_fallback=False,
        )
    if any(p in error_msg for p in _MODEL_NOT_FOUND_PATTERNS):
        return result_fn(
            FailoverReason.model_not_found,
            retryable=False,
            should_fallback=True,
        )
    # Generic 404 with no "model not found" signal — could be a wrong
    # endpoint path (common with local llama.cpp / Ollama / vLLM when
    # the URL is slightly misconfigured), a proxy routing glitch, or
    # a transient backend issue.  Classifying these as model_not_found
    # silently falls back to a different provider and tells the model
    # the model is missing, which is wrong and wastes a turn.  Treat
    # as unknown so the retry loop surfaces the real error instead.
    return result_fn(
        FailoverReason.unknown,
        retryable=True,
    )


def _classify_402(error_msg: str, result_fn: _ClassifierBuilder) -> ClassifiedError:
    """Disambiguate 402: billing exhaustion vs transient usage limit.

    The key insight from OpenClaw: some 402s are transient rate limits
    disguised as payment errors.  "Usage limit, try again in 5 minutes"
    is NOT a billing problem — it's a periodic quota that resets.
    """
    has_usage_limit = any(p in error_msg for p in _USAGE_LIMIT_PATTERNS)
    has_transient_signal = any(p in error_msg for p in _USAGE_LIMIT_TRANSIENT_SIGNALS)

    if has_usage_limit and has_transient_signal:
        return result_fn(
            FailoverReason.rate_limit,
            retryable=True,
            should_rotate_credential=True,
            should_fallback=True,
        )

    return result_fn(
        FailoverReason.billing,
        retryable=False,
        should_rotate_credential=True,
        should_fallback=True,
    )


def _classify_400(
    error_msg: str,
    error_code: str,
    body: dict,
    *,
    provider: str,  # noqa: ARG001 — kept for signature parity with _classify_by_status
    model: str,  # noqa: ARG001 — kept for signature parity with _classify_by_status
    approx_tokens: int,
    context_length: int,
    num_messages: int = 0,
    result_fn: _ClassifierBuilder,
) -> ClassifiedError:
    """Classify 400 Bad Request — context overflow, format error, or generic."""
    # Multimodal tool content rejected from 400.  Must be checked BEFORE
    # image_too_large because the recovery is different (strip image parts
    # from tool messages, mark the model as no-list-tool-content for the
    # rest of the session) and BEFORE context_overflow because some of the
    # patterns ("text is not set") are ambiguous in isolation but become
    # specific when combined with a 400 on a request known to contain
    # multimodal tool content.
    if any(p in error_msg for p in _MULTIMODAL_TOOL_CONTENT_PATTERNS):
        return result_fn(
            FailoverReason.multimodal_tool_content_unsupported,
            retryable=True,
        )

    # Image-too-large from 400 (Anthropic's 5 MB per-image check fires this way).
    # Must be checked BEFORE context_overflow because messages can trip both
    # patterns ("exceeds" + "image") and image-shrink is a cheaper recovery.
    if any(p in error_msg for p in _IMAGE_TOO_LARGE_PATTERNS):
        return result_fn(
            FailoverReason.image_too_large,
            retryable=True,
        )

    # Invalid encrypted reasoning replay blob (OpenAI Responses API).  Must be
    # checked BEFORE context_overflow because some surfaces emit messages that
    # contain context-like phrasing ("encrypted content … could not be
    # verified") which could otherwise trip the context_overflow heuristics.
    # ``error_msg`` is lowercased upstream — match accordingly.
    error_code_lower = (error_code or "").lower()
    if (
        error_code_lower == "invalid_encrypted_content"
        or "invalid_encrypted_content" in error_msg
        or ("encrypted content for item" in error_msg and "could not be verified" in error_msg)
    ):
        return result_fn(
            FailoverReason.invalid_encrypted_content,
            retryable=True,
            should_fallback=False,
        )

    if any(p in error_msg for p in _CONTEXT_OVERFLOW_PATTERNS):
        return result_fn(
            FailoverReason.context_overflow,
            retryable=True,
            should_compress=True,
        )

    # Some providers return model-not-found as 400 instead of 404 (e.g. OpenRouter).
    if any(p in error_msg for p in _PROVIDER_POLICY_BLOCKED_PATTERNS):
        return result_fn(
            FailoverReason.provider_policy_blocked,
            retryable=False,
            should_fallback=False,
        )
    if any(p in error_msg for p in _MODEL_NOT_FOUND_PATTERNS):
        return result_fn(
            FailoverReason.model_not_found,
            retryable=False,
            should_fallback=True,
        )

    # Some providers return rate limit / billing errors as 400 instead of 429/402.
    if any(p in error_msg for p in _RATE_LIMIT_PATTERNS):
        return result_fn(
            FailoverReason.rate_limit,
            retryable=True,
            should_rotate_credential=True,
            should_fallback=True,
        )
    if any(p in error_msg for p in _BILLING_PATTERNS):
        return result_fn(
            FailoverReason.billing,
            retryable=False,
            should_rotate_credential=True,
            should_fallback=True,
        )

    # Provider couldn't fetch the URL embedded in an image_url part.
    # Distinguished from generic format_error because retrying unchanged
    # won't help; the user needs a curated message saying so.
    if any(p in error_msg for p in _ATTACHMENT_FETCH_PATTERNS):
        return result_fn(
            FailoverReason.attachment_fetch_failed,
            retryable=False,
            should_fallback=False,
        )

    # Generic 400 + large session → probable context overflow
    # Anthropic sometimes returns a bare "Error" message when context is too large
    err_body_msg = _extract_body_message(body)
    is_generic = len(err_body_msg) < 30 or err_body_msg in {"error", ""}
    # Absolute token/message-count thresholds are only a proxy for smaller
    # context windows.  Large-context sessions can have many messages while
    # still being far below their actual token budget.
    is_large = approx_tokens > context_length * 0.4 or (context_length <= 256000 and (approx_tokens > 80000 or num_messages > 80))

    if is_generic and is_large:
        return result_fn(
            FailoverReason.context_overflow,
            retryable=True,
            should_compress=True,
        )

    return result_fn(
        FailoverReason.format_error,
        retryable=False,
        should_fallback=True,
    )


# ── Error code classification ───────────────────────────────────────────


def _classify_by_error_code(
    error_code: str,
    error_msg: str,  # noqa: ARG001 — shared helper signature; message patterns handled by callers
    result_fn: _ClassifierBuilder,
) -> ClassifiedError | None:
    """Classify by structured error codes from the response body."""
    code_lower = error_code.lower()
    if code_lower in _RATE_LIMIT_ERROR_CODES:
        return result_fn(
            FailoverReason.rate_limit,
            retryable=True,
            should_rotate_credential=True,
        )
    if code_lower in _BILLING_ERROR_CODES:
        return result_fn(
            FailoverReason.billing,
            retryable=False,
            should_rotate_credential=True,
            should_fallback=True,
        )
    if code_lower in _MODEL_NOT_FOUND_ERROR_CODES:
        return result_fn(
            FailoverReason.model_not_found,
            retryable=False,
            should_fallback=True,
        )
    if code_lower in _CONTEXT_OVERFLOW_ERROR_CODES:
        return result_fn(
            FailoverReason.context_overflow,
            retryable=True,
            should_compress=True,
        )
    if code_lower == "invalid_encrypted_content":
        return result_fn(
            FailoverReason.invalid_encrypted_content,
            retryable=True,
            should_fallback=False,
        )
    return None


# ── Message pattern classification ──────────────────────────────────────


def _classify_by_message(
    error_msg: str,
    error_type: str,  # noqa: ARG001 — shared helper signature; patterns keyed on message text
    *,
    approx_tokens: int,  # noqa: ARG001 — kept for signature parity with _classify_400
    context_length: int,  # noqa: ARG001 — kept for signature parity with _classify_400
    result_fn: _ClassifierBuilder,
) -> ClassifiedError | None:
    """Classify based on error message patterns when no status code is available."""
    if any(p in error_msg for p in _PAYLOAD_TOO_LARGE_PATTERNS):
        return result_fn(
            FailoverReason.payload_too_large,
            retryable=True,
            should_compress=True,
        )

    if any(p in error_msg for p in _MULTIMODAL_TOOL_CONTENT_PATTERNS):
        return result_fn(
            FailoverReason.multimodal_tool_content_unsupported,
            retryable=True,
        )

    if any(p in error_msg for p in _IMAGE_TOO_LARGE_PATTERNS):
        return result_fn(
            FailoverReason.image_too_large,
            retryable=True,
        )

    # Usage-limit patterns need disambiguation: a transient signal
    # ("try again", "resets at", …) means it's a periodic quota, not
    # billing exhaustion.
    if any(p in error_msg for p in _USAGE_LIMIT_PATTERNS):
        has_transient_signal = any(p in error_msg for p in _USAGE_LIMIT_TRANSIENT_SIGNALS)
        if has_transient_signal:
            return result_fn(
                FailoverReason.rate_limit,
                retryable=True,
                should_rotate_credential=True,
                should_fallback=True,
            )
        return result_fn(
            FailoverReason.billing,
            retryable=False,
            should_rotate_credential=True,
            should_fallback=True,
        )

    if any(p in error_msg for p in _BILLING_PATTERNS):
        return result_fn(
            FailoverReason.billing,
            retryable=False,
            should_rotate_credential=True,
            should_fallback=True,
        )

    if any(p in error_msg for p in _RATE_LIMIT_PATTERNS):
        return result_fn(
            FailoverReason.rate_limit,
            retryable=True,
            should_rotate_credential=True,
            should_fallback=True,
        )

    if any(p in error_msg for p in _CONTEXT_OVERFLOW_PATTERNS):
        return result_fn(
            FailoverReason.context_overflow,
            retryable=True,
            should_compress=True,
        )

    # Auth errors should NOT be retried directly — the credential is invalid
    # and retrying with the same key will always fail.  Set retryable=False
    # so the caller triggers credential rotation or provider fallback
    # rather than an immediate retry loop.
    if any(p in error_msg for p in _AUTH_PATTERNS):
        return result_fn(
            FailoverReason.auth,
            retryable=False,
            should_rotate_credential=True,
            should_fallback=True,
        )

    if any(p in error_msg for p in _PROVIDER_POLICY_BLOCKED_PATTERNS):
        return result_fn(
            FailoverReason.provider_policy_blocked,
            retryable=False,
            should_fallback=False,
        )

    if any(p in error_msg for p in _MODEL_NOT_FOUND_PATTERNS):
        return result_fn(
            FailoverReason.model_not_found,
            retryable=False,
            should_fallback=True,
        )

    # Generic exception types (e.g. RuntimeError) raised by local shims or
    # custom providers that internally wrap a subprocess/HTTP timeout.
    # Classified as transport timeout so the retry loop rebuilds the
    # client instead of treating the turn as an empty model response.
    if any(p in error_msg for p in _TIMEOUT_MESSAGE_PATTERNS):
        return result_fn(FailoverReason.timeout, retryable=True)

    return None


# ── Helpers ─────────────────────────────────────────────────────────────


_CAUSE_CHAIN_MAX_DEPTH = 5


def _build_error_message(error: Exception, body: dict) -> str:
    """Combine str(error), the body message, and OpenRouter's wrapped metadata.raw."""
    raw_msg = str(error).lower()
    body_msg = ""
    metadata_msg = ""
    if isinstance(body, dict):
        err_obj = body.get("error", {})
        if isinstance(err_obj, dict):
            body_msg = str(err_obj.get("message") or "").lower()
            metadata = err_obj.get("metadata", {})
            if isinstance(metadata, dict):
                raw_json = metadata.get("raw") or ""
                if isinstance(raw_json, str) and raw_json.strip():
                    inner = safe_json_loads(raw_json)
                    if isinstance(inner, dict):
                        inner_err = inner.get("error", {})
                        if isinstance(inner_err, dict):
                            metadata_msg = str(inner_err.get("message") or "").lower()
        if not body_msg:
            body_msg = str(body.get("message") or "").lower()
    parts = [raw_msg]
    if body_msg and body_msg not in raw_msg:
        parts.append(body_msg)
    if metadata_msg and metadata_msg not in raw_msg and metadata_msg not in body_msg:
        parts.append(metadata_msg)
    return " ".join(parts)


def _extract_body_message(body: dict) -> str:
    """Lowercased message from the body (error.message or top-level message)."""
    if not isinstance(body, dict):
        return ""
    err_obj = body.get("error", {})
    if isinstance(err_obj, dict):
        msg = str(err_obj.get("message") or "").strip().lower()
        if msg:
            return msg
    return str(body.get("message") or "").strip().lower()


def _extract_status_code(error: Exception) -> int | None:
    """Walk the error and its cause chain to find an HTTP status code."""
    current = error
    for _ in range(_CAUSE_CHAIN_MAX_DEPTH):
        code = getattr(current, "status_code", None)
        if isinstance(code, int):
            return code
        # Some SDKs use .status instead of .status_code
        code = getattr(current, "status", None)
        if isinstance(code, int) and 100 <= code < 600:
            return code
        cause = getattr(current, "__cause__", None) or getattr(current, "__context__", None)
        if cause is None or cause is current:
            break
        current = cause
    return None


def _extract_error_body(error: Exception) -> dict:
    """Extract the structured error body from an SDK exception."""
    body = getattr(error, "body", None)
    if isinstance(body, dict):
        return body
    response = getattr(error, "response", None)
    if response is not None:
        try:
            json_body = response.json()
            if isinstance(json_body, dict):
                return json_body
        except Exception:
            pass
    return {}


def _extract_error_code(body: dict) -> str:
    """Extract an error code string from the response body."""
    if not body:
        return ""

    def _code_from_payload(payload) -> str:
        if not isinstance(payload, dict):
            return ""
        payload_error = payload.get("error", {})
        if isinstance(payload_error, dict):
            nested = payload_error.get("code") or payload_error.get("type") or ""
            if isinstance(nested, str) and nested.strip() and nested.strip() != "400":
                return nested.strip()
        code = payload.get("code") or payload.get("error_code") or ""
        if isinstance(code, (str, int)):
            text = str(code).strip()
            if text and text != "400":
                return text
        return ""

    error_obj = body.get("error", {})
    if isinstance(error_obj, dict):
        code = error_obj.get("code") or error_obj.get("type") or ""
        if isinstance(code, str) and code.strip() and code.strip() != "400":
            return code.strip()

        # Some providers wrap the real JSON error body as a string inside
        # error.message — peek into it for a nested code (e.g. Responses API
        # surfaces ``invalid_encrypted_content`` this way).
        message = error_obj.get("message")
        if isinstance(message, str) and message.strip().startswith("{"):
            inner = safe_json_loads(message)
            nested_code = _code_from_payload(inner)
            if nested_code:
                return nested_code

    code = body.get("code") or body.get("error_code") or ""
    if isinstance(code, (str, int)):
        text = str(code).strip()
        if text and text != "400":
            return text
    return ""


def _extract_message(error: Exception, body: dict) -> str:
    """Extract the most informative error message."""
    if body:
        error_obj = body.get("error", {})
        if isinstance(error_obj, dict):
            msg = error_obj.get("message", "")
            if isinstance(msg, str) and msg.strip():
                return msg.strip()[:500]
        msg = body.get("message", "")
        if isinstance(msg, str) and msg.strip():
            return msg.strip()[:500]
    return redact_sensitive_text(str(error))[:500]


_RESET_HEADER_PATTERN = re.compile(r"^\d+(\.\d+)?$")


def _extract_suggested_delay(error: Exception) -> float | None:
    """Extract rate limit reset / retry-after delay from headers in seconds."""
    response = getattr(error, "response", None)
    if response is None:
        return None
    headers = getattr(response, "headers", {})
    if not hasattr(headers, "get"):
        return None
    reset_str = headers.get("x-ratelimit-reset") or headers.get("retry-after")
    if not reset_str:
        return None
    try:
        return _parse_delay(str(reset_str).strip().lower())
    except Exception:
        return None


def _parse_delay(value: str) -> float | None:
    """Parse a delay value with optional unit suffix (ms/s/m/h) or bare number."""
    if value.endswith("ms"):
        return float(value[:-2]) / 1000.0
    if value.endswith("s"):
        return float(value[:-1])
    if value.endswith("m"):
        return float(value[:-1]) * 60.0
    if value.endswith("h"):
        return float(value[:-1]) * 3600.0
    if _RESET_HEADER_PATTERN.match(value):
        return float(value)
    return None
