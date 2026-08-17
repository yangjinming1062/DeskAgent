from typing import Any, Literal

from .logger import get_logger

logger = get_logger("paid_calls")


def log_paid_call(provider: str, kind: str, *, task_id: str | None = None, user_id: int | None = None, level: Literal["info", "debug", "warning"] = "info", **extra: Any) -> None:
    """Breadcrumb for real-money provider calls (LLM / image / video / 3D).

    ``task_id`` events log at INFO — one line per billed task, cheap to keep
    always-on; bulky payloads like URL lists default to DEBUG. Async providers
    log submit + result separately so a crashed download still leaves the
    task_id + URLs recoverable from the log.
    """
    fields: dict[str, Any] = {"provider": provider, "kind": kind, **extra}
    if user_id is not None:
        fields["user_id"] = user_id
    if task_id:
        fields["task_id"] = task_id
    getattr(logger, level)(f"paid call: {kind}", extra=fields)
