import httpx
import pytest
from components import SETTINGS
from components.network import download_capped, is_safe_outbound


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


class TestSsrfAllowedCidrs:
    """SSRF_ALLOWED_CIDRS 仅豁免匹配 IP 的保留网段拦截，域名/云元数据黑名单不受影响。"""

    def test_private_ip_refused_by_default(self, monkeypatch):
        monkeypatch.setattr(SETTINGS, "ssrf_allowed_cidrs", "")
        safe, _ = is_safe_outbound("127.0.0.1")
        assert not safe

    def test_fake_ip_range_bypasses_reserved_check(self, monkeypatch):
        monkeypatch.setattr(SETTINGS, "ssrf_allowed_cidrs", "127.0.0.0/8")
        safe, reason = is_safe_outbound("127.0.0.1")
        assert safe, reason

    def test_bypass_is_range_scoped(self, monkeypatch):
        monkeypatch.setattr(SETTINGS, "ssrf_allowed_cidrs", "198.18.0.0/15")
        safe, _ = is_safe_outbound("127.0.0.1")
        assert not safe

    def test_blocked_hostname_survives_full_bypass(self, monkeypatch):
        monkeypatch.setattr(SETTINGS, "ssrf_allowed_cidrs", "0.0.0.0/0")
        safe, reason = is_safe_outbound("metadata.google.internal")
        assert not safe and "blocked hostname" in reason

    def test_unparseable_entries_ignored(self, monkeypatch):
        monkeypatch.setattr(SETTINGS, "ssrf_allowed_cidrs", "not-a-cidr, 127.0.0.0/8")
        safe, _ = is_safe_outbound("127.0.0.1")
        assert safe
