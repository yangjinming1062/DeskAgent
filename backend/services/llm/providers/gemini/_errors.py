from collections.abc import Iterator

from .._provider_errors import raise_for_provider_response
from ..base import ProviderError


def raise_for_gemini_response(resp, *, provider: str, model: str) -> dict:
    """See :func:`providers._provider_errors.raise_for_provider_response`."""
    return raise_for_provider_response(resp, family=provider, model=model)


def iter_parts(body: dict) -> Iterator[dict]:
    """Yield each ``content.parts[]`` entry across all candidates."""
    for candidate in body.get("candidates") or []:
        for part in (candidate.get("content") or {}).get("parts") or []:
            yield part
