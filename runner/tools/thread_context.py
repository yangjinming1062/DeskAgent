import contextvars
import logging
from collections.abc import Callable
from typing import Any

from .terminal import get_sudo_password_callback, set_sudo_password_callback

logger = logging.getLogger(__name__)


def propagate_context_to_thread(target: Callable[..., Any]) -> Callable[..., Any]:
    ctx = contextvars.copy_context()
    parent_sudo_cb = None
    try:
        parent_sudo_cb = get_sudo_password_callback()
    except Exception:
        logger.debug("Could not capture parent approval/sudo callbacks", exc_info=True)

    def _runner(*args: Any, **kwargs: Any) -> Any:
        def _inner() -> Any:
            if parent_sudo_cb is not None:
                try:
                    set_sudo_password_callback(parent_sudo_cb)
                except Exception:
                    logger.debug("Failed to install propagated approval/sudo callbacks", exc_info=True)
            try:
                return target(*args, **kwargs)
            finally:
                try:
                    set_sudo_password_callback(None)
                except Exception:
                    logger.debug("Failed to clear propagated approval/sudo callbacks", exc_info=True)

        return ctx.run(_inner)

    return _runner
