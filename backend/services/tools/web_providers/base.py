import abc
from typing import Any


class WebSearchProvider(abc.ABC):
    """Abstract base class for a web search/extract backend.

    Subclasses must implement :meth:`is_available` and at least one of
    :meth:`search` / :meth:`extract`.

    Credentials (API keys, instance URLs, …) are **injected per-call** by
    the dispatcher in :mod:`tools.web_tools` from per-user
    ``user_settings``; subclasses should accept them as keyword args on
    ``__init__`` and fall back to ``os.getenv(...)`` when the dispatcher
    has nothing better to offer.
    """

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Stable short identifier used in web backend config keys
        (e.g. ``brave-free``, ``ddgs``, ``tavily``)."""

    @property
    def display_name(self) -> str:
        """Human-readable label shown in ``spiritagent tools``."""
        return self.name

    @abc.abstractmethod
    def is_available(self) -> bool:
        """Return True when this provider can service calls.

        Must be cheap (env var, optional dep, instance URL) — runs at
        tool-registration time and on every ``spiritagent tools`` paint. No network I/O.
        """

    def supports_search(self) -> bool:
        return True

    def supports_extract(self) -> bool:
        """Return True if this provider implements :meth:`extract`."""
        return False

    async def search(self, query: str, limit: int = 5) -> dict[str, Any]:
        """Execute a web search. Override when :meth:`supports_search` is True."""
        raise NotImplementedError(f"{self.name} does not support search (override supports_search)")

    async def extract(self, urls: list[str], **kwargs: Any) -> Any:
        """Extract content from one or more URLs.

        Return shape::

            [
                {"url", "title", "content", "raw_content",
                 "metadata": dict?, "error": str?},  # error only on per-URL failure
                ...
            ]

        Subclasses wrapping a sync HTTP library must use
        :func:`asyncio.to_thread` (or equivalent) inside ``search``/``extract``
        so the asyncio loop is not blocked. ``kwargs`` may carry forward-compat
        fields (``format``, ``include_raw``, ``max_chars``); ignore unknown keys.
        """
        raise NotImplementedError(f"{self.name} does not support extract (override supports_extract)")

    def get_setup_schema(self) -> dict[str, Any]:
        """Return provider metadata for the ``spiritagent tools`` picker.

        Override to expose API key prompts, badges, and instance URL fields.
        """
        return {"name": self.display_name, "badge": "", "tag": "", "env_vars": []}

    def missing_credential_message(self) -> str | None:
        """Actionable user-facing message for ``is_available() == False``.

        Called by the dispatcher only when this provider was the explicit
        search/extract selection — not when it's the silent fallback.
        ``None`` (default) falls back to the generic "X is not configured"
        message; override to point the user at the right settings UI.
        """
        return None
