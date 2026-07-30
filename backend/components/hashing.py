import base64
import hashlib
from pathlib import Path


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha512_b64(path: Path) -> str:
    """SHA-512 of a file as base64 (electron-updater format)."""
    h = hashlib.sha512()
    with path.open("rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return base64.b64encode(h.digest()).decode()


def normalize_sha512(value: str) -> str:
    """Convert legacy 128-char hex SHA-512 to base64; pass through otherwise."""
    return base64.b64encode(bytes.fromhex(value)).decode() if len(value) == 128 else value
