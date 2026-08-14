import hashlib
import re
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import HTTPException, Request, Response
from starlette.responses import StreamingResponse

_RANGE_PATTERN = re.compile(r"^bytes=(\d*)-(\d*)$")
_CHUNK_SIZE = 256 * 1024  # 256 KB


def compute_file_sha256(path: Path | str) -> str:
    """Compute SHA-256 hash of a file on disk in streaming chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(_CHUNK_SIZE):
            h.update(chunk)
    return h.hexdigest()


def compute_bytes_sha256(data: bytes) -> str:
    """Compute SHA-256 hash of raw bytes."""
    return hashlib.sha256(data).hexdigest()


def _parse_range_header(range_header: str, file_size: int) -> tuple[int, int] | None:
    """Parses a Range header string like 'bytes=0-499', 'bytes=500-', or 'bytes=-500'.

    Returns (start, end) inclusive, or None if invalid/unsatisfiable.
    """
    match = _RANGE_PATTERN.match(range_header.strip())
    if not match:
        return None

    raw_start, raw_end = match.groups()

    if raw_start and raw_end:
        start = int(raw_start)
        end = int(raw_end)
        if start > end or start >= file_size:
            return None
        return start, min(end, file_size - 1)

    if raw_start and not raw_end:
        start = int(raw_start)
        if start >= file_size:
            return None
        return start, file_size - 1

    if not raw_start and raw_end:
        suffix = int(raw_end)
        if suffix <= 0:
            return None
        start = max(0, file_size - suffix)
        return start, file_size - 1

    return None


async def serve_ranged_file(request: Request, file_path: Path, media_type: str, *, content_sha256: str | None = None) -> Response:
    """Serves a file with full HTTP Range (206/416), ETag, and immutable cache headers.

    Streaming generators are used so even large models (hundreds of MBs) never
    get loaded into backend process memory at once.
    """
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    file_size = file_path.stat().st_size
    sha256 = content_sha256 or compute_file_sha256(file_path)
    etag = f'"{sha256}"'

    base_headers = {"Accept-Ranges": "bytes", "ETag": etag, "Cache-Control": "public, max-age=31536000, immutable", "X-Content-Sha256": sha256}

    # Conditional 304 Not Modified check
    if_none_match = request.headers.get("if-none-match")
    if if_none_match and if_none_match.strip() in (etag, sha256, "*"):
        return Response(status_code=304, headers=base_headers)

    range_header = request.headers.get("range")

    # If no Range header, stream the entire file with 200 OK
    if not range_header:

        async def full_file_iterator() -> AsyncIterator[bytes]:
            with open(file_path, "rb") as f:
                while chunk := f.read(_CHUNK_SIZE):
                    yield chunk

        headers = {**base_headers, "Content-Length": str(file_size)}
        return StreamingResponse(full_file_iterator(), status_code=200, media_type=media_type, headers=headers)

    # Process Range request
    range_bounds = _parse_range_header(range_header, file_size)
    if range_bounds is None:
        # 416 Range Not Satisfiable
        return Response(status_code=416, headers={**base_headers, "Content-Range": f"bytes */{file_size}"})

    start, end = range_bounds
    chunk_length = end - start + 1

    async def ranged_iterator() -> AsyncIterator[bytes]:
        with open(file_path, "rb") as f:
            f.seek(start)
            remaining = chunk_length
            while remaining > 0:
                to_read = min(_CHUNK_SIZE, remaining)
                chunk = f.read(to_read)
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    ranged_headers = {**base_headers, "Content-Range": f"bytes {start}-{end}/{file_size}", "Content-Length": str(chunk_length)}

    return StreamingResponse(ranged_iterator(), status_code=206, media_type=media_type, headers=ranged_headers)
