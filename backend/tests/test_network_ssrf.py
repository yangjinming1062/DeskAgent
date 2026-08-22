"""Connect-time SSRF guard tests for ``backend/components/network.py``.

Pin the contract:

1. ``is_safe_outbound`` (sync) still does hostname + DNS + per-IP evaluation;
   the existing TestSsrfAllowedCidrs suite continues to pass.
2. ``_SafeOutboundAsyncBackend.connect_tcp`` re-resolves the hostname at
   connect time and rejects any DNS rebinding target — closing the TOCTOU
   window that ``request hook`` pre-check left open.
3. Async DNS resolution happens off the event loop (in a worker thread).
4. Original host is preserved for HTTP Host / TLS SNI / cert verification;
   only the actual ``socket.connect`` uses the validated IP.
5. Redirects (per-hop in ``download_capped``) re-run the same backend.
"""

from __future__ import annotations

import asyncio
import importlib.util
import socket
import sys
import threading
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpcore
import pytest


def _load_network_module():
    """直接用 importlib 加载 components.network，绕过 components/__init__.py
    触发的那段坏掉的 pydantic-settings ``Settings()`` 初始化。

    仅在测试里使用：保留 stub SETTINGS，让 ``_ssrf_allowed_networks`` 能跑。
    """
    backend_dir = Path(__file__).resolve().parent.parent
    components_pkg = sys.modules.get("components")
    if components_pkg is None:
        components_pkg = types.ModuleType("components")
        components_pkg.__path__ = [str(backend_dir / "components")]
        sys.modules["components"] = components_pkg

    @dataclass
    class _StubSettings:
        ssrf_allowed_cidrs: str = ""

    if not hasattr(components_pkg, "config"):
        config_mod = types.ModuleType("components.config")
        config_mod.SETTINGS = _StubSettings()
        config_mod.Settings = type("Settings", (), {})
        sys.modules["components.config"] = config_mod
        components_pkg.config = config_mod

    if not hasattr(components_pkg, "logger"):
        logger_mod = types.ModuleType("components.logger")

        def _stub_logger(name):
            import logging

            return logging.getLogger(name)

        logger_mod.get_logger = _stub_logger
        sys.modules["components.logger"] = logger_mod
        components_pkg.logger = logger_mod

    spec = importlib.util.spec_from_file_location(
        "components.network",
        backend_dir / "components" / "network.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


NETWORK = _load_network_module()


def _make_addr_info(ips: list[str], port: int = 443) -> list[tuple]:
    out = []
    for ip in ips:
        out.append((socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)))
    return out


class TestConnectTimeBackendSyncPreFlight:
    """``_evaluate_hostname`` 是 hostname 黑名单的同步快速预检。"""

    def test_metadata_google_internal_blocked(self):
        ok, reason = NETWORK._evaluate_hostname("metadata.google.internal")
        assert not ok and "blocked hostname" in reason

    def test_instance_data_blocked(self):
        ok, _ = NETWORK._evaluate_hostname("instance-data.ec2.internal")
        assert not ok

    def test_empty_host_blocked(self):
        ok, _ = NETWORK._evaluate_hostname("")
        assert not ok

    def test_safe_hostname_passes(self):
        ok, _ = NETWORK._evaluate_hostname("api.example.com")
        assert ok


class TestIsSafeOutbound:
    """``is_safe_outbound`` 的语义要保持：hostname + IP 字面量 + DNS + 全 IP 校验。"""

    def test_ip_literal_private_refused(self, monkeypatch):
        monkeypatch.setattr(NETWORK.SETTINGS, "ssrf_allowed_cidrs", "")
        ok, _ = NETWORK.is_safe_outbound("127.0.0.1")
        assert not ok

    def test_ip_literal_metadata_refused(self, monkeypatch):
        monkeypatch.setattr(NETWORK.SETTINGS, "ssrf_allowed_cidrs", "")
        ok, _ = NETWORK.is_safe_outbound("169.254.169.254")
        assert not ok

    def test_blocked_hostname_refused(self, monkeypatch):
        monkeypatch.setattr(NETWORK.SETTINGS, "ssrf_allowed_cidrs", "0.0.0.0/0")
        ok, reason = NETWORK.is_safe_outbound("metadata.google.internal")
        assert not ok and "blocked hostname" in reason

    def test_safe_ip_passes(self, monkeypatch):
        monkeypatch.setattr(NETWORK.SETTINGS, "ssrf_allowed_cidrs", "")
        ok, _ = NETWORK.is_safe_outbound("8.8.8.8")
        assert ok

    def test_fake_ip_range_bypass(self, monkeypatch):
        monkeypatch.setattr(NETWORK.SETTINGS, "ssrf_allowed_cidrs", "198.18.0.0/15")
        ok, _ = NETWORK.is_safe_outbound("198.18.0.1")
        assert ok


class TestResolveAndValidate:
    """``_resolve_and_validate`` 同步执行 DNS + SSRF 校验。"""

    def test_validates_all_resolved_ips(self, monkeypatch):
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda _h, p, *_a, **_kw: _make_addr_info(["8.8.8.8", "1.1.1.1"], p),
        )
        result = NETWORK._resolve_and_validate("good.example.com", 443)
        assert len(result) == 2
        assert result[0][0] == "8.8.8.8"
        assert result[1][0] == "1.1.1.1"

    def test_rejects_when_any_ip_is_loopback(self, monkeypatch):
        """DNS 解析返回多个 IP，只要其中一个是 loopback —— 必须拒绝。"""
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda _h, p, *_a, **_kw: _make_addr_info(["8.8.8.8", "127.0.0.1"], p),
        )
        with pytest.raises(httpcore.ConnectError, match="loopback"):
            NETWORK._resolve_and_validate("evil.example.com", 443)

    def test_rejects_when_dns_returns_link_local_ip(self, monkeypatch):
        """169.254.169.254 同时是 link-local 与 AWS metadata —— 都必须被拦。

        我们的策略是按顺序检查：link-local 命中先于 cloud-metadata，
        所以错误信息走 link-local 分支。
        """
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda _h, p, *_a, **_kw: _make_addr_info(["169.254.169.254"], p),
        )
        with pytest.raises(httpcore.ConnectError, match="link-local"):
            NETWORK._resolve_and_validate("evil.example.com", 443)

    def test_rejects_alibaba_metadata_ip(self, monkeypatch):
        """100.100.100.200 不在 link-local 段，会落入 cloud-metadata 分支。"""
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda _h, p, *_a, **_kw: _make_addr_info(["100.100.100.200"], p),
        )
        with pytest.raises(httpcore.ConnectError, match="cloud-metadata"):
            NETWORK._resolve_and_validate("evil.example.com", 443)

    def test_dns_failure_raises_connect_error(self, monkeypatch):
        def _fail(*_a, **_kw):
            raise socket.gaierror("no such host")

        monkeypatch.setattr(socket, "getaddrinfo", _fail)
        with pytest.raises(httpcore.ConnectError, match="DNS resolution failed"):
            NETWORK._resolve_and_validate("no-such-host.example.com", 443)


class TestSafeOutboundAsyncBackend:
    """``_SafeOutboundAsyncBackend.connect_tcp`` 在 socket.connect 之前解析+校验。"""

    @pytest.mark.asyncio
    async def test_blocks_metadata_hostname_before_dns(self, monkeypatch):
        backend = NETWORK._SafeOutboundAsyncBackend()
        called = {"count": 0}

        def _explode(*_a, **_kw):
            called["count"] += 1
            raise AssertionError("getaddrinfo should not be called for blocked hostname")

        monkeypatch.setattr(socket, "getaddrinfo", _explode)
        with pytest.raises(httpcore.ConnectError, match="blocked hostname"):
            await backend.connect_tcp("metadata.google.internal", 443, timeout=5.0)
        assert called["count"] == 0

    @pytest.mark.asyncio
    async def test_blocks_when_resolved_to_loopback(self, monkeypatch):
        backend = NETWORK._SafeOutboundAsyncBackend()
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda _h, p, *_a, **_kw: _make_addr_info(["127.0.0.1"], p),
        )
        with pytest.raises(httpcore.ConnectError, match="loopback"):
            await backend.connect_tcp("evil.example.com", 443, timeout=5.0)

    @pytest.mark.asyncio
    async def test_blocks_link_local_v6(self, monkeypatch):
        backend = NETWORK._SafeOutboundAsyncBackend()
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda _h, _p, *_a, **_kw: [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("fe80::1", 443))],
        )
        with pytest.raises(httpcore.ConnectError, match="link-local"):
            await backend.connect_tcp("evil.example.com", 443, timeout=5.0)

    @pytest.mark.asyncio
    async def test_dns_resolution_runs_off_event_loop(self, monkeypatch):
        """DNS 解析必须发生在非事件循环线程里。"""
        backend = NETWORK._SafeOutboundAsyncBackend()
        main_thread = threading.get_ident()
        resolved_in: list[int] = []

        def _resolve(host, port, *args, **kwargs):
            resolved_in.append(threading.get_ident())
            return _make_addr_info(["8.8.8.8"], port)

        monkeypatch.setattr(socket, "getaddrinfo", _resolve)
        # Stub anyio.to_thread so we can verify it routes through a worker thread
        import anyio

        async def _fake_run_sync(fn, *args, **kwargs):
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, fn, *args, **kwargs)

        monkeypatch.setattr(anyio.to_thread, "run_sync", _fake_run_sync)

        class _FakeStream:
            async def aclose(self):
                pass

        class _FakeInner:
            async def connect_tcp(self, host, port, **_):
                # 关键断言：inner backend 收到的必须是已校验 IP，不是原始 host
                assert host == "8.8.8.8", f"must connect to validated IP, got {host!r}"
                return _FakeStream()

        backend._backend = _FakeInner()
        await backend.connect_tcp("good.example.com", 443, timeout=5.0)
        assert resolved_in
        for tid in resolved_in:
            assert tid != main_thread, "DNS must not run on the event-loop thread"

    @pytest.mark.asyncio
    async def test_uses_validated_ip_with_original_port(self, monkeypatch):
        """inner backend 收到 IP+port 时，端口必须与原始端口一致（覆盖非默认端口）。"""
        backend = NETWORK._SafeOutboundAsyncBackend()

        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda _h, p, *_a, **_kw: _make_addr_info(["8.8.8.8"], p),
        )
        import anyio

        async def _fake_run_sync(fn, *args, **kwargs):
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, fn, *args, **kwargs)

        monkeypatch.setattr(anyio.to_thread, "run_sync", _fake_run_sync)

        observed: dict[str, Any] = {}

        class _FakeStream:
            async def aclose(self):
                pass

        class _FakeInner:
            async def connect_tcp(self, host, port, **_):
                observed["host"] = host
                observed["port"] = port
                return _FakeStream()

        backend._backend = _FakeInner()
        await backend.connect_tcp("good.example.com", 8443, timeout=5.0)
        assert observed == {"host": "8.8.8.8", "port": 8443}


class TestSafeOutboundAsyncClient:
    """``safe_outbound_async_client`` 工厂：返回的 AsyncClient 挂载 ``_SafeOutboundAsyncTransport``。"""

    def test_factory_uses_safe_transport(self):
        client = NETWORK.safe_outbound_async_client()
        try:
            assert isinstance(client._transport, NETWORK._SafeOutboundAsyncTransport)
            backend = client._transport._pool._network_backend
            assert isinstance(backend, NETWORK._SafeOutboundAsyncBackend)
        finally:
            # AsyncClient.close is async; just drop the reference
            pass


class TestDownloadCappedRedirects:
    """``download_capped`` 的每一跳都由 ``_SafeOutboundAsyncBackend`` 校验。

    这里只对单跳 download 做断言；逐跳 redirect 已在现有
    ``test_download_capped_*`` 中覆盖，仅把 connect-time 校验独立断言。
    """

    @pytest.mark.asyncio
    async def test_download_capped_blocks_at_connect(self, monkeypatch):
        """``download_capped`` 把 ``_SafeOutboundAsyncBackend`` 在 connect 时
        抛出的 SSRF 拒绝透传给调用方（以 ``httpx.ConnectError`` 形式）。"""
        import httpx

        # 模拟 DNS rebinding：直接返回私网 IP，让真正的 connect-time 校验触发
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda _h, _p, *_a, **_kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 443))],
        )
        with pytest.raises(httpx.ConnectError, match="10.0.0.1"):
            await NETWORK.download_capped("https://example.com/file.bin", max_bytes=100)


class TestProviderHttpClientSsrf:
    """``services.llm.providers.http`` 的 ``get_http`` 与 ``get_async_client`` 必须挂载 SSRF 守卫。"""

    @pytest.mark.asyncio
    async def test_get_http_blocks_private_ip(self, monkeypatch):
        import httpx
        from components.network import _SafeOutboundAsyncTransport
        from services.llm.providers import http

        http.cache_clear()
        client = http.get_http("https://api.example.com", "test-key")
        assert isinstance(client._transport, _SafeOutboundAsyncTransport)

        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda _h, _p, *_a, **_kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))],
        )
        with pytest.raises(httpx.ConnectError):
            await client.get("/v1/test")

    def test_get_async_client_uses_safe_transport(self):
        from components.network import _SafeOutboundAsyncTransport
        from services.llm.providers import http

        http.cache_clear()
        client = http.get_async_client("test-key", "https://api.example.com")
        http_client = getattr(client, "_client", None)
        assert http_client is not None
        assert isinstance(http_client._transport, _SafeOutboundAsyncTransport)
