import httpx
import pytest

from components.network import download_capped


@pytest.mark.asyncio
async def test_download_capped_success(monkeypatch):
    data = b"Hello Secure World"

    def _mock_stream(self, method, url, *args, **kwargs):
        class MockStreamResponse:
            is_redirect = False
            status_code = 200

            def raise_for_status(self):
                pass

            async def aiter_bytes(self):
                yield data[:5]
                yield data[5:]

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                pass

        return MockStreamResponse()

    monkeypatch.setattr(httpx.AsyncClient, "stream", _mock_stream)
    monkeypatch.setattr("components.network.is_safe_outbound", lambda host: (True, ""))

    res = await download_capped("https://example.com/file.bin", max_bytes=100)
    assert res == data


@pytest.mark.asyncio
async def test_download_capped_exceeds_limit(monkeypatch):
    data = b"A" * 200

    def _mock_stream(self, method, url, *args, **kwargs):
        class MockStreamResponse:
            is_redirect = False
            status_code = 200

            def raise_for_status(self):
                pass

            async def aiter_bytes(self):
                yield data

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                pass

        return MockStreamResponse()

    monkeypatch.setattr(httpx.AsyncClient, "stream", _mock_stream)
    monkeypatch.setattr("components.network.is_safe_outbound", lambda host: (True, ""))

    with pytest.raises(ValueError, match="exceeded size limit"):
        await download_capped("https://example.com/large.bin", max_bytes=100)


@pytest.mark.asyncio
async def test_download_capped_unsupported_scheme():
    with pytest.raises(ValueError, match="unsupported URL scheme"):
        await download_capped("ftp://example.com/file.bin", max_bytes=100)


@pytest.mark.asyncio
async def test_download_capped_ssrf_blocked(monkeypatch):
    monkeypatch.setattr(
        "components.network.is_safe_outbound",
        lambda host: (False, "private IP blocked"),
    )
    with pytest.raises(httpx.ConnectError, match="SSRF check failed"):
        await download_capped("https://10.0.0.1/secret", max_bytes=100)


@pytest.mark.asyncio
async def test_download_capped_redirect_success(monkeypatch):
    step = 0

    def _mock_stream(self, method, url, *args, **kwargs):
        nonlocal step
        step += 1

        class MockStreamResponse:
            def __init__(self, s):
                self.s = s
                if self.s == 1:
                    self.is_redirect = True
                    self.status_code = 302
                    self.headers = {"location": "/relative/path.bin"}
                else:
                    self.is_redirect = False
                    self.status_code = 200
                    self.headers = {}

            def raise_for_status(self):
                pass

            async def aiter_bytes(self):
                yield b"redirected_content"

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                pass

        return MockStreamResponse(step)

    monkeypatch.setattr(httpx.AsyncClient, "stream", _mock_stream)
    monkeypatch.setattr("components.network.is_safe_outbound", lambda host: (True, ""))

    res = await download_capped("https://example.com/initial", max_bytes=100)
    assert res == b"redirected_content"
    assert step == 2


@pytest.mark.asyncio
async def test_download_capped_redirect_downgrade_blocked(monkeypatch):
    def _mock_stream(self, method, url, *args, **kwargs):
        class MockStreamResponse:
            is_redirect = True
            status_code = 302
            headers = {"location": "http://example.com/downgrade"}

            def raise_for_status(self):
                pass

            async def aiter_bytes(self):
                yield b""

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                pass

        return MockStreamResponse()

    monkeypatch.setattr(httpx.AsyncClient, "stream", _mock_stream)
    monkeypatch.setattr("components.network.is_safe_outbound", lambda host: (True, ""))

    with pytest.raises(
        ValueError, match="refusing redirect downgrade from HTTPS to HTTP"
    ):
        await download_capped("https://example.com/secure", max_bytes=100)
