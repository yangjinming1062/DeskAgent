import asyncio
import socket
import threading
from typing import Any

import httpcore
import httpx
import pytest

from utils.url_safety import (
    SafeAsyncHTTPTransport,
    SafeHTTPTransport,
    _SafeAsyncBackend,
    _SafeSyncBackend,
    async_is_safe_url,
    is_safe_url,
)


def _make_addr_info(ips: list[str], port: int = 443) -> list[tuple]:
    """伪造 ``socket.getaddrinfo`` 的返回值，全部用 IPv4 单地址。"""
    out = []
    for ip in ips:
        out.append((socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)))
    return out


class TestSafeHttpTransportPrefilter:
    """``SafeHTTPTransport.handle_request`` 仍然要做 URL 字符串预检。"""

    def test_blocks_metadata_ip_literal(self):
        transport = SafeHTTPTransport()
        request = httpx.Request("GET", "http://169.254.169.254/latest/meta-data/")
        with pytest.raises(ValueError, match="SSRF guard"):
            transport.handle_request(request)

    def test_blocks_blocked_hostname(self):
        transport = SafeHTTPTransport()
        request = httpx.Request("GET", "http://metadata.google.internal/foo")
        with pytest.raises(ValueError, match="SSRF guard"):
            transport.handle_request(request)


class TestSafeSyncBackendConnectTime:
    """``_SafeSyncBackend.connect_tcp`` 在每次 socket.connect 之前重新解析+校验。"""

    def test_rejects_resolved_loopback_at_connect(self, monkeypatch):
        """首次解析安全、connect 阶段 getaddrinfo 拿到 loopback —— 必须拦截。"""
        backend = _SafeSyncBackend()

        def _evil_resolve(host, port, *args, **kwargs):
            # 模拟 DNS rebinding：第一次返回安全 IP，紧接着返回 loopback
            return _make_addr_info(["127.0.0.1"], port)

        monkeypatch.setattr(socket, "getaddrinfo", _evil_resolve)
        with pytest.raises(httpcore.ConnectError, match="SSRF guard"):
            backend.connect_tcp("evil.example.com", 443, timeout=5.0)

    def test_rejects_link_local_v6(self, monkeypatch):
        backend = _SafeSyncBackend()
        monkeypatch.setattr(socket, "getaddrinfo", lambda h, p, *a, **kw: _make_addr_info(["fe80::1"], p))
        with pytest.raises(httpcore.ConnectError, match="SSRF guard"):
            backend.connect_tcp("evil.example.com", 443, timeout=5.0)

    def test_connects_to_validated_ip(self, monkeypatch):
        """安全域名：必须直接连到我们解析出的安全 IP，原始 host 仅用于 HTTP/TLS 层。"""
        backend = _SafeSyncBackend()
        captured: dict[str, Any] = {}

        # fake socket so we can capture the address without touching the network
        class _FakeSock:
            def __init__(self, family=socket.AF_INET, type_=socket.SOCK_STREAM, proto=0):
                self._closed = False

            def setsockopt(self, *args, **kwargs):
                pass

            def settimeout(self, _timeout):
                pass

            def bind(self, _addr):
                pass

            def connect(self, addr):
                captured["addr"] = addr

            def close(self):
                self._closed = True

        # 8.8.8.8 = Google DNS, is_private() returns False
        monkeypatch.setattr(socket, "getaddrinfo", lambda h, p, *a, **kw: _make_addr_info(["8.8.8.8"], p))
        monkeypatch.setattr(socket, "socket", _FakeSock)

        from httpcore._backends.sync import SyncStream

        stream = backend.connect_tcp("good.example.com", 443, timeout=5.0)
        try:
            assert isinstance(stream, SyncStream)
            assert captured["addr"] == ("8.8.8.8", 443)
        finally:
            stream.close()


class TestSafeAsyncBackendConnectTime:
    """``_SafeAsyncBackend.connect_tcp`` 同样做 connect-time 校验；DNS 解析放工作线程。"""

    @pytest.mark.asyncio
    async def test_rejects_loopback_at_connect(self, monkeypatch):
        backend = _SafeAsyncBackend()
        monkeypatch.setattr(socket, "getaddrinfo", lambda h, p, *a, **kw: _make_addr_info(["127.0.0.1"], p))
        # Stub anyio.to_thread so we still exercise the validation logic synchronously
        import anyio

        async def _fake_run_sync(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        monkeypatch.setattr(anyio.to_thread, "run_sync", _fake_run_sync)
        with pytest.raises(httpcore.ConnectError, match="SSRF guard"):
            await backend.connect_tcp("evil.example.com", 443, timeout=5.0)

    @pytest.mark.asyncio
    async def test_blocks_hostname_before_dns(self, monkeypatch):
        """hostname 黑名单必须在 DNS 解析之前被拦截，否则会因慢解析浪费线程时间。"""
        backend = _SafeAsyncBackend()
        called = {"count": 0}

        def _explode(*_a, **_kw):
            called["count"] += 1
            raise AssertionError("getaddrinfo should not be called for blocked hostname")

        monkeypatch.setattr(socket, "getaddrinfo", _explode)
        with pytest.raises(httpcore.ConnectError, match="(?i)blocked.*hostname"):
            await backend.connect_tcp("metadata.google.internal", 443, timeout=5.0)
        assert called["count"] == 0

    @pytest.mark.asyncio
    async def test_dns_resolution_runs_in_worker_thread(self, monkeypatch):
        """DNS 解析必须发生在非事件循环线程里。"""
        backend = _SafeAsyncBackend()
        main_thread = threading.get_ident()
        resolved_in: list[int] = []

        def _resolve(host, port, *args, **kwargs):
            resolved_in.append(threading.get_ident())
            return _make_addr_info(["8.8.8.8"], port)

        monkeypatch.setattr(socket, "getaddrinfo", _resolve)
        # Stub anyio.to_thread so we can assert the call site AND run in a worker thread
        import anyio

        async def _fake_run_sync(fn, *args, **kwargs):
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, fn, *args, **kwargs)

        monkeypatch.setattr(anyio.to_thread, "run_sync", _fake_run_sync)

        # Inject a fake inner backend so we don't actually open a TCP socket
        class _FakeStream:
            async def aclose(self):
                pass

        class _FakeInner:
            async def connect_tcp(self, host, port, **_):
                assert host == "8.8.8.8", "must connect to validated IP, not the host"
                return _FakeStream()

        backend._backend = _FakeInner()

        await backend.connect_tcp("good.example.com", 443, timeout=5.0)
        assert resolved_in, "DNS resolver was never invoked"
        for tid in resolved_in:
            assert tid != main_thread, "DNS must not run on the event-loop thread"


class TestRedirectPerHopValidation:
    """重定向的每一跳都触发 ``_SafeSyncBackend.connect_tcp`` —— 同样要解析+校验。

    这里直接对 ``_SafeSyncBackend.connect_tcp`` 在重定向后的目标上做断言，
    跳过复杂的 httpx 重定向链路（httpx 的 follow_redirects 默认关闭，而
    safe_outbound_async_client 显式 follow_redirects=False）；上层
    ``check_redirect_url_safety`` 已在独立测试中覆盖 URL 层面过滤。
    """

    def test_redirect_hop_to_metadata_blocked(self, monkeypatch):
        """模拟重定向第二跳落到云元数据 IP —— 必须在 connect 时被拦。"""
        backend = _SafeSyncBackend()
        # 重定向到 metadata.google.internal（已在黑名单）→ host 检查先行拦截
        with pytest.raises(httpcore.ConnectError, match="internal hostname"):
            backend.connect_tcp("metadata.google.internal", 443, timeout=5.0)

    def test_redirect_hop_to_loopback_blocked(self, monkeypatch):
        """重定向到任意域名但解析结果是 loopback —— 必须在 connect 时被拦。"""
        backend = _SafeSyncBackend()
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda h, p, *a, **kw: _make_addr_info(["127.0.0.1"], p),
        )
        with pytest.raises(httpcore.ConnectError, match="SSRF guard"):
            backend.connect_tcp("redirected.example.org", 443, timeout=5.0)


class TestSafeAsyncClientFactory:
    """``create_safe_async_client`` 应当挂上 ``_SafeAsyncBackend``。"""

    def test_async_client_uses_safe_backend(self):
        client = SafeAsyncHTTPTransport()._pool._network_backend
        assert isinstance(client, _SafeAsyncBackend)

    def test_sync_client_uses_safe_backend(self):
        client = SafeHTTPTransport()._pool._network_backend
        assert isinstance(client, _SafeSyncBackend)


class TestPrefilterStillWorks:
    """既有 ``is_safe_url`` / ``async_is_safe_url`` 预检行为要保持。"""

    def test_is_safe_url_loopback(self, monkeypatch):
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda h, p, *a, **kw: _make_addr_info(["127.0.0.1"], 443),
        )
        assert is_safe_url("http://127.0.0.1/") is False

    @pytest.mark.asyncio
    async def test_async_is_safe_url_loopback(self, monkeypatch):
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda h, p, *a, **kw: _make_addr_info(["127.0.0.1"], 443),
        )
        assert await async_is_safe_url("http://127.0.0.1/") is False
