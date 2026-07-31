from collections.abc import Iterator


def iter_parts(body: dict) -> Iterator[dict]:
    """Yield each ``content.parts[]`` entry across all candidates."""
    for candidate in body.get("candidates") or []:
        for part in (candidate.get("content") or {}).get("parts") or []:
            yield part
