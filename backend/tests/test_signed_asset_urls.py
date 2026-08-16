import time

import pytest

from services.companion import (
    asset_store,
    build_signed_asset_url,
    build_signed_avatar_url,
    verify_signed_asset_request,
    verify_signed_avatar_request,
)


def test_signed_asset_url_round_trip():
    url = build_signed_asset_url(42, "idle_video_abc.mp4")
    # The query string is part of the URL.
    assert "?" in url
    assert "expires=" in url
    assert "sig=" in url

    # Parse it back to the verify function.
    from urllib.parse import parse_qs, urlparse

    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    assert verify_signed_asset_request(
        42, "idle_video_abc.mp4", int(qs["expires"][0]), qs["sig"][0]
    )


def test_signed_asset_url_rejects_wrong_user():
    url = build_signed_asset_url(42, "idle_video_abc.mp4")
    from urllib.parse import parse_qs, urlparse

    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    assert not verify_signed_asset_request(
        99,  # different user — must not be valid for the same URL
        "idle_video_abc.mp4",
        int(qs["expires"][0]),
        qs["sig"][0],
    )


def test_signed_asset_url_rejects_wrong_filename():
    url = build_signed_asset_url(42, "idle_video_abc.mp4")
    from urllib.parse import parse_qs, urlparse

    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    assert not verify_signed_asset_request(
        42,
        "idle_video_xxx.mp4",  # different filename
        int(qs["expires"][0]),
        qs["sig"][0],
    )


def test_signed_asset_url_rejects_tampered_sig():
    url = build_signed_asset_url(42, "idle_video_abc.mp4")
    from urllib.parse import parse_qs, urlparse

    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    assert not verify_signed_asset_request(
        42, "idle_video_abc.mp4", int(qs["expires"][0]), "deadbeef" + qs["sig"][0][8:]
    )


def test_signed_asset_url_rejects_expired():
    from services.companion.asset_store import _sign

    # Forge an expired URL by recomputing the signature against a
    # past timestamp.
    past = int(time.time()) - 60
    sig = _sign(42, "idle_video_abc.mp4", past)
    assert not verify_signed_asset_request(42, "idle_video_abc.mp4", past, sig)


def test_signed_avatar_url_round_trip():
    url = build_signed_avatar_url("fileid123", "png")
    assert "?expires=" in url
    assert "&sig=" in url
    from urllib.parse import parse_qs, urlparse

    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    assert verify_signed_avatar_request(
        "fileid123.png", int(qs["expires"][0]), qs["sig"][0]
    )


def test_signed_avatar_url_rejects_wrong_filename():
    url = build_signed_avatar_url("fileid123", "png")
    from urllib.parse import parse_qs, urlparse

    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    assert not verify_signed_avatar_request(
        "other.png",  # wrong filename
        int(qs["expires"][0]),
        qs["sig"][0],
    )


def test_signer_key_raises_outside_test_mode(monkeypatch):
    """P2-12 belt-and-suspenders: if the deploy reaches ``_signing_key()``
    with an empty signing key AND test mode was not flipped on, refuse to
    sign URLs with the public test key. Catch misconfiguration loudly."""
    monkeypatch.setattr(asset_store.SETTINGS, "companion_asset_signing_key", "")
    monkeypatch.setattr(asset_store, "_TEST_MODE", False)

    with pytest.raises(RuntimeError, match="empty outside test mode"):
        asset_store._signing_key()
