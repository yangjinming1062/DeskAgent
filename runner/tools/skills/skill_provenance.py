import contextvars

_write_origin: contextvars.ContextVar[str] = contextvars.ContextVar("skill_write_origin", default="foreground")
BACKGROUND_REVIEW = "background_review"


def is_background_review() -> bool:
    return _write_origin.get() == BACKGROUND_REVIEW
