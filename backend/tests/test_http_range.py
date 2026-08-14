import hashlib
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from services.companion.http_range import compute_bytes_sha256, compute_file_sha256, serve_ranged_file

app = FastAPI()


@app.get("/test-file")
async def get_test_file(request: Request, path: str):
    return await serve_ranged_file(request, Path(path), "model/gltf-binary")


def test_compute_sha256(tmp_path: Path):
    content = b"test 3d model binary content"
    f = tmp_path / "test.glb"
    f.write_bytes(content)

    expected = hashlib.sha256(content).hexdigest()
    assert compute_file_sha256(f) == expected
    assert compute_bytes_sha256(content) == expected


def test_serve_full_file_200(tmp_path: Path):
    content = b"0123456789" * 100  # 1000 bytes
    f = tmp_path / "model.glb"
    f.write_bytes(content)

    client = TestClient(app)
    resp = client.get(f"/test-file?path={f}")

    assert resp.status_code == 200
    assert resp.content == content
    assert resp.headers["accept-ranges"] == "bytes"
    assert resp.headers["content-length"] == "1000"
    assert "etag" in resp.headers
    assert resp.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert resp.headers["x-content-sha256"] == hashlib.sha256(content).hexdigest()


def test_serve_range_start_end_206(tmp_path: Path):
    content = b"abcdefghijklmnopqrstuvwxyz"
    f = tmp_path / "alphabet.glb"
    f.write_bytes(content)

    client = TestClient(app)
    # Request bytes 0 to 4 (5 bytes: 'abcde')
    resp = client.get(f"/test-file?path={f}", headers={"Range": "bytes=0-4"})

    assert resp.status_code == 206
    assert resp.content == b"abcde"
    assert resp.headers["content-range"] == "bytes 0-4/26"
    assert resp.headers["content-length"] == "5"
    assert resp.headers["accept-ranges"] == "bytes"


def test_serve_range_start_only_206(tmp_path: Path):
    content = b"abcdefghijklmnopqrstuvwxyz"
    f = tmp_path / "alphabet.glb"
    f.write_bytes(content)

    client = TestClient(app)
    # Request bytes 20 to end ('uvwxyz')
    resp = client.get(f"/test-file?path={f}", headers={"Range": "bytes=20-"})

    assert resp.status_code == 206
    assert resp.content == b"uvwxyz"
    assert resp.headers["content-range"] == "bytes 20-25/26"
    assert resp.headers["content-length"] == "6"


def test_serve_range_suffix_only_206(tmp_path: Path):
    content = b"abcdefghijklmnopqrstuvwxyz"
    f = tmp_path / "alphabet.glb"
    f.write_bytes(content)

    client = TestClient(app)
    # Request last 4 bytes ('wxyz')
    resp = client.get(f"/test-file?path={f}", headers={"Range": "bytes=-4"})

    assert resp.status_code == 206
    assert resp.content == b"wxyz"
    assert resp.headers["content-range"] == "bytes 22-25/26"
    assert resp.headers["content-length"] == "4"


def test_serve_range_out_of_bounds_416(tmp_path: Path):
    content = b"abcdefghijklmnopqrstuvwxyz"
    f = tmp_path / "alphabet.glb"
    f.write_bytes(content)

    client = TestClient(app)
    # Request start out of bounds
    resp = client.get(f"/test-file?path={f}", headers={"Range": "bytes=100-"})

    assert resp.status_code == 416
    assert resp.headers["content-range"] == "bytes */26"


def test_serve_if_none_match_304(tmp_path: Path):
    content = b"test cached content"
    f = tmp_path / "cached.glb"
    f.write_bytes(content)
    sha = hashlib.sha256(content).hexdigest()

    client = TestClient(app)
    resp = client.get(f"/test-file?path={f}", headers={"If-None-Match": f'"{sha}"'})
    assert resp.status_code == 304


def test_serve_missing_file_404(tmp_path: Path):
    client = TestClient(app)
    resp = client.get(f"/test-file?path={tmp_path / 'nonexistent.glb'}")
    assert resp.status_code == 404
