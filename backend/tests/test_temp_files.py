import json
import time

from components import SETTINGS, get_file_path, save_file


def test_get_file_path_rejects_malformed_file_id():
    assert get_file_path("../secret") is None
    assert get_file_path("nested/id") is None


def test_get_file_path_enforces_ttl(tmp_path, monkeypatch):
    monkeypatch.setattr(SETTINGS, "data_dir", str(tmp_path))
    file_id, _ = save_file(b"temporary", "session", "image/png", "png")

    meta_path = tmp_path / "temp-media" / f"{file_id}.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["created_at"] = time.time() - SETTINGS.temp_file_ttl_hours * 3600 - 1
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    assert get_file_path(file_id) is None


def test_get_file_path_rejects_metadata_escape(tmp_path, monkeypatch):
    monkeypatch.setattr(SETTINGS, "data_dir", str(tmp_path))
    file_id, _ = save_file(b"temporary", "session", "image/png", "png")
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"outside")

    meta_path = tmp_path / "temp-media" / f"{file_id}.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["path"] = str(outside)
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    assert get_file_path(file_id) is None
    assert outside.exists()


def test_get_file_path_rejects_malformed_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(SETTINGS, "data_dir", str(tmp_path))
    file_id, _ = save_file(b"temporary", "session", "image/png", "png")

    meta_path = tmp_path / "temp-media" / f"{file_id}.json"
    meta_path.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")

    assert get_file_path(file_id) is None


def test_direct_fallback_enforces_ttl(tmp_path, monkeypatch):
    import os

    monkeypatch.setattr(SETTINGS, "data_dir", str(tmp_path))
    file_id = "legacy-file"
    media_path = tmp_path / "temp-media" / f"{file_id}.png"
    media_path.parent.mkdir(parents=True)
    media_path.write_bytes(b"legacy")
    expired_at = time.time() - SETTINGS.temp_file_ttl_hours * 3600 - 1
    os.utime(media_path, (expired_at, expired_at))

    assert get_file_path(file_id) is None
    assert not media_path.exists()
