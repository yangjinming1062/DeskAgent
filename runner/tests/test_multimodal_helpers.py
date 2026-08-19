import pytest

from tools.multimodal import helpers


@pytest.mark.asyncio
async def test_download_media_rejects_stream_over_limit_without_writing(monkeypatch, tmp_path):
    class FakeResponse:
        headers = {}
        url = "https://example.test/image.jpg"

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_value, traceback):
            return False

        def raise_for_status(self):
            return None

        async def aiter_bytes(self):
            yield b"x" * 4
            yield b"x" * 4

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_value, traceback):
            return False

        async def stream(self, method, url, headers):
            return FakeResponse()

    monkeypatch.setattr(helpers.httpx, "AsyncClient", lambda **kwargs: FakeClient())
    monkeypatch.setattr(helpers, "check_website_access", lambda url: None)
    destination = tmp_path / "downloads" / "image.jpg"

    with pytest.raises(ValueError, match="Image too large"):
        await helpers._download_media(
            "https://example.test/image.jpg",
            destination,
            accept="image/*",
            max_bytes=5,
            timeout=1,
            media_label="image",
            max_retries=1,
        )

    assert not destination.exists()
