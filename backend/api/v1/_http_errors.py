from fastapi import HTTPException
from services.llm import ClassifiedError


def classified_http_exception(classified: ClassifiedError) -> HTTPException:
    """Build an HTTPException from a classified upstream error.

    Preserve the upstream status code for all 4xx (401 for invalid key,
    403 for quota, 422 for bad params, etc.) so the renderer can route
    per-code remediation flows.  Only 5xx is normalised to 500.
    """
    upstream = classified.status_code or 500
    if upstream >= 500:
        http_status = 500
    elif upstream < 400:
        # Non-HTTP or misclassified status — treat as server error
        http_status = 500
    else:
        http_status = upstream
    return HTTPException(
        status_code=http_status, detail={"error": classified.message or classified.reason.value, "reason": classified.reason.value, "status": classified.status_code}
    )


def missing_config_http(svc_label: str, status_code: int = 400) -> HTTPException:
    """Standard 400/501 envelope for ``MissingLlmConfigError`` raised by
    the chain resolver when no provider is configured for ``svc_label``."""
    return HTTPException(status_code=status_code, detail={"error": f"{svc_label} provider not configured", "reason": "missing_config", "status": status_code})
