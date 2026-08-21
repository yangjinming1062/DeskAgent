#!/usr/bin/env python3
import asyncio
import contextlib
import json
import logging
import os
import re
import secrets
import socket
import stat
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
from mcp.client.auth.oauth2 import OAuthClientProvider
from mcp.client.auth.utils import (
    build_oauth_authorization_server_metadata_discovery_urls,
    build_protected_resource_metadata_discovery_urls,
    create_oauth_metadata_request,
    handle_auth_metadata_response,
    handle_protected_resource_response,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthMetadata, OAuthToken
from pydantic import AnyUrl

from utils import get_spiritagent_home, secure_parent_dir

logger = logging.getLogger(__name__)


class OAuthNonInteractiveError(RuntimeError):
    """非交互环境下 OAuth 流程无法完成（如无浏览器/超时）时抛出。"""

    pass


def _get_token_dir() -> Path:
    try:
        return Path(get_spiritagent_home()) / "mcp-tokens"
    except Exception:
        return Path.home() / ".spiritagent" / "mcp-tokens"


def _safe_filename(name: str) -> str:
    return re.sub(r"[^\w\-]", "_", name).strip("_")[:128] or "default"


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _is_interactive() -> bool:
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def _can_open_browser() -> bool:
    if os.environ.get("SSH_CLIENT") or os.environ.get("SSH_TTY"):
        return False
    if os.name == "nt":
        return True
    try:
        if os.uname().sysname == "Darwin":
            return True
    except AttributeError:
        pass
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _read_json(path: Path) -> dict | None:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read %s: %s", path, exc)
    return None


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    secure_parent_dir(path)
    tmp = path.with_suffix(f".tmp.{os.getpid()}.{secrets.token_hex(4)}")
    try:
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, default=str)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


class SpiritAgentTokenStorage:
    """符合 MCP SDK TokenStorage 协议的 SPIRITAGENT 端 token 持久化实现。"""

    def __init__(self, server_name: str) -> None:
        self._server_name = _safe_filename(server_name)
        self._token_dir = _get_token_dir()

    def _tokens_path(self) -> Path:
        return self._token_dir / f"{self._server_name}.json"

    def _client_info_path(self) -> Path:
        return self._token_dir / f"{self._server_name}.client.json"

    def _meta_path(self) -> Path:
        return self._token_dir / f"{self._server_name}.meta.json"

    async def get_tokens(self) -> OAuthToken | None:
        if not (data := _read_json(self._tokens_path())):
            return None
        if (exp_at := data.pop("expires_at", None)) is not None:
            data["expires_in"] = int(max(exp_at - time.time(), 0))
        elif (exp_in := data.get("expires_in")) is not None:
            try:
                if (mtime := self._tokens_path().stat().st_mtime) is not None:
                    data["expires_in"] = int(max(mtime + int(exp_in) - time.time(), 0))
            except (TypeError, ValueError, OSError):
                pass
        try:
            return OAuthToken.model_validate(data)
        except (ValueError, TypeError, KeyError) as exc:
            logger.warning("Corrupt tokens at %s: %s", self._tokens_path(), exc)
            return None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        payload = tokens.model_dump(mode="json", exclude_none=True)
        if (exp_in := payload.get("expires_in")) is not None:
            with contextlib.suppress(TypeError, ValueError):
                payload["expires_at"] = time.time() + int(exp_in)
        _write_json(self._tokens_path(), payload)

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        if not (data := _read_json(self._client_info_path())):
            return None
        try:
            return OAuthClientInformationFull.model_validate(data)
        except (ValueError, TypeError, KeyError) as exc:
            logger.warning("Corrupt client info at %s: %s", self._client_info_path(), exc)
            return None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        _write_json(self._client_info_path(), client_info.model_dump(mode="json", exclude_none=True))

    def save_oauth_metadata(self, metadata: OAuthMetadata) -> None:
        _write_json(self._meta_path(), metadata.model_dump(exclude_none=True, mode="json"))

    def load_oauth_metadata(self) -> OAuthMetadata | None:
        if not (data := _read_json(self._meta_path())):
            return None
        try:
            return OAuthMetadata.model_validate(data)
        except (ValueError, TypeError, KeyError) as exc:
            logger.warning("Corrupt OAuth metadata at %s: %s", self._meta_path(), exc)
            return None

    def remove(self) -> None:
        for p in (self._tokens_path(), self._client_info_path(), self._meta_path()):
            p.unlink(missing_ok=True)

    def has_cached_tokens(self) -> bool:
        return self._tokens_path().exists()


def remove_oauth_tokens(server_name: str) -> None:
    """从磁盘与缓存中清除指定 MCP 服务器的 OAuth 凭据。"""
    SpiritAgentTokenStorage(server_name).remove()
    logger.info("OAuth tokens removed for '%s'", server_name)


def _make_callback_handler() -> tuple[type, dict]:
    result = {"auth_code": None, "state": None, "error": None}

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            params = parse_qs(urlparse(self.path).query)
            result.update({"auth_code": params.get("code", [None])[0], "state": params.get("state", [None])[0], "error": params.get("error", [None])[0]})
            body = (
                "<html><body><h2>Authorization Successful</h2><p>You can close this tab and return to SpiritAgent.</p></body></html>"
                if result["auth_code"]
                else f"<html><body><h2>Authorization Failed</h2><p>Error: {result['error'] or 'unknown'}</p></body></html>"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, fmt: str, *args: Any) -> None:
            logger.debug("OAuth callback: %s", fmt % args)

    return _Handler, result


def _make_redirect_handler(port: int):
    async def _redirect_handler(authorization_url: str) -> None:
        print(f"\n  MCP OAuth: authorization required.\n  Open this URL in your browser:\n\n    {authorization_url}\n", file=sys.stderr)
        if os.getenv("SSH_CLIENT") or os.getenv("SSH_TTY"):
            print(
                f"  Remote session detected. After you authorize, the provider redirects to\n"
                f"    http://127.0.0.1:{port}/callback\n"
                f"  which only the listener on THIS machine can receive. Forward the port first:\n"
                f"         ssh -N -L {port}:127.0.0.1:{port} <user>@<this-host>\n",
                file=sys.stderr,
            )
        if _can_open_browser():
            try:
                if webbrowser.open(authorization_url):
                    print("  (Browser opened automatically.)\n", file=sys.stderr)
                    return
            except Exception:
                pass
        print("  (Please open the URL manually.)\n", file=sys.stderr)

    return _redirect_handler


def _make_wait_for_callback(port: int):
    async def _wait_for_callback() -> tuple[str, str | None]:
        handler_cls, result = _make_callback_handler()
        try:
            server = HTTPServer(("127.0.0.1", port), handler_cls)
        except OSError as exc:
            raise OAuthNonInteractiveError("OAuth callback timed out — could not bind callback port.") from exc
        # Polling handle_request (via server.timeout) instead of one blocking
        # call: the serve thread reliably exits once `stop` is set, rather
        # than lingering in accept() holding the port forever.
        server.timeout = 0.5
        stop = threading.Event()

        def _serve() -> None:
            while not stop.is_set() and result["auth_code"] is None and result["error"] is None:
                server.handle_request()

        threading.Thread(target=_serve, daemon=True).start()
        try:
            deadline = time.monotonic() + 300.0
            while time.monotonic() < deadline:
                if result["auth_code"] is not None or result["error"] is not None:
                    break
                await asyncio.sleep(0.5)
        finally:
            stop.set()
            server.server_close()

        if result["error"]:
            raise RuntimeError(f"OAuth authorization failed: {result['error']}")
        if result["auth_code"] is None:
            raise OAuthNonInteractiveError("OAuth callback timed out.")
        return result["auth_code"], result["state"]

    return _wait_for_callback


def _configure_callback_port(cfg: dict) -> int:
    requested = int(cfg.get("redirect_port", 0))
    return _find_free_port() if requested == 0 else requested


def _build_client_metadata(cfg: dict, port: int) -> OAuthClientMetadata:
    metadata_kwargs = {
        "client_name": cfg.get("client_name", "SpiritAgent Agent"),
        "redirect_uris": [AnyUrl(f"http://127.0.0.1:{port}/callback")],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "client_secret_post" if cfg.get("client_secret") else "none",
    }
    if scope := cfg.get("scope"):
        metadata_kwargs["scope"] = scope
    return OAuthClientMetadata.model_validate(metadata_kwargs)


def _maybe_preregister_client(storage: SpiritAgentTokenStorage, cfg: dict, client_metadata: OAuthClientMetadata, port: int) -> None:
    if not (client_id := cfg.get("client_id")):
        return
    info_dict = {
        "client_id": client_id,
        "redirect_uris": [f"http://127.0.0.1:{port}/callback"],
        "grant_types": client_metadata.grant_types,
        "response_types": client_metadata.response_types,
        "token_endpoint_auth_method": client_metadata.token_endpoint_auth_method,
    }
    for key in ("client_secret", "client_name", "scope"):
        if val := cfg.get(key):
            info_dict[key] = val
    _write_json(storage._client_info_path(), OAuthClientInformationFull.model_validate(info_dict).model_dump(mode="json", exclude_none=True))


def _make_spiritagent_provider_class() -> type:
    class SpiritAgentMCPOAuthProvider(OAuthClientProvider):
        def __init__(self, *args: Any, server_name: str = "", **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self._spiritagent_server_name = server_name

        async def _initialize(self) -> None:
            await super()._initialize()
            if (tokens := self.context.current_tokens) is not None and tokens.expires_in is not None:
                self.context.update_token_expiry(tokens)

            storage = self.context.storage
            if isinstance(storage, SpiritAgentTokenStorage) and self.context.oauth_metadata is None and (meta := storage.load_oauth_metadata()) is not None:
                self.context.oauth_metadata = meta
                logger.debug("MCP OAuth '%s': restored metadata from disk", self._spiritagent_server_name)

            if tokens is not None and self.context.oauth_metadata is None:
                try:
                    await self._prefetch_oauth_metadata()
                except Exception as exc:
                    logger.debug("MCP OAuth '%s': pre-flight metadata discovery failed: %s", self._spiritagent_server_name, exc)

        async def _prefetch_oauth_metadata(self) -> None:
            server_url = self.context.server_url
            async with httpx.AsyncClient(timeout=10.0) as client:
                for url in build_protected_resource_metadata_discovery_urls(None, server_url):
                    try:
                        resp = await client.send(create_oauth_metadata_request(url))
                    except httpx.HTTPError as exc:
                        logger.debug("MCP OAuth '%s': PRM discovery to %s failed: %s", self._spiritagent_server_name, url, exc)
                        continue
                    if prm := await handle_protected_resource_response(resp):
                        self.context.protected_resource_metadata = prm
                        if prm.authorization_servers:
                            self.context.auth_server_url = str(prm.authorization_servers[0])
                        break

                for url in build_oauth_authorization_server_metadata_discovery_urls(self.context.auth_server_url, server_url):
                    try:
                        resp = await client.send(create_oauth_metadata_request(url))
                    except httpx.HTTPError as exc:
                        logger.debug("MCP OAuth '%s': ASM discovery to %s failed: %s", self._spiritagent_server_name, url, exc)
                        continue
                    ok, asm = await handle_auth_metadata_response(resp)
                    if not ok:
                        break
                    if asm:
                        self.context.oauth_metadata = asm
                        if isinstance(storage := self.context.storage, SpiritAgentTokenStorage):
                            storage.save_oauth_metadata(asm)
                        logger.debug("MCP OAuth '%s': ASM discovered token_endpoint=%s", self._spiritagent_server_name, asm.token_endpoint)
                        break

        def _persist_oauth_metadata_if_changed(self) -> None:
            if (meta := self.context.oauth_metadata) is None:
                return
            if not isinstance(storage := self.context.storage, SpiritAgentTokenStorage):
                return
            existing = storage.load_oauth_metadata()
            if existing is None or str(existing.token_endpoint) != str(meta.token_endpoint):
                storage.save_oauth_metadata(meta)

        async def async_auth_flow(self, request) -> None:
            try:
                await get_manager().invalidate_if_disk_changed(self._spiritagent_server_name)
            except Exception as exc:
                logger.debug("MCP OAuth '%s': disk-watch failed: %s", self._spiritagent_server_name, exc)

            inner = super().async_auth_flow(request)
            try:
                outgoing = await anext(inner)
                while True:
                    incoming = yield outgoing
                    outgoing = await inner.asend(incoming)
            except StopAsyncIteration:
                self._persist_oauth_metadata_if_changed()
                return

    return SpiritAgentMCPOAuthProvider


SPIRITAGENT_PROVIDER_CLS = _make_spiritagent_provider_class()


@dataclass
class _ProviderEntry:
    server_url: str
    oauth_config: dict | None
    provider: Any | None = None
    last_mtime_ns: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    pending_401: dict[str, asyncio.Future[bool]] = field(default_factory=dict)


class MCPOAuthManager:
    """管理 MCP OAuth provider 的进程内单例，跨服务器复用 provider 与 401 处理状态。"""

    def __init__(self) -> None:
        self._entries: dict[str, _ProviderEntry] = {}
        self._entries_lock = threading.Lock()
        self._bg_tasks: set[asyncio.Task] = set()

    def get_or_build_provider(self, server_name: str, server_url: str, oauth_config: dict | None) -> Any | None:
        with self._entries_lock:
            entry = self._entries.get(server_name)
            if entry is not None and entry.server_url != server_url:
                logger.info("MCP OAuth '%s': URL changed, discarding cache", server_name)
                entry = None
            if entry is None:
                entry = _ProviderEntry(server_url=server_url, oauth_config=oauth_config)
                self._entries[server_name] = entry
            provider = entry.provider
        if provider is None:
            # Built outside the entries lock — _maybe_preregister_client
            # writes the client-info JSON to disk here.
            provider = self._build_provider(server_name, entry)
            with self._entries_lock:
                if entry.provider is None:
                    entry.provider = provider
                provider = entry.provider
        return provider

    def _build_provider(self, server_name: str, entry: _ProviderEntry) -> Any | None:
        cfg = dict(entry.oauth_config or {})
        storage = SpiritAgentTokenStorage(server_name)
        if not _is_interactive() and not storage.has_cached_tokens():
            logger.warning("MCP OAuth '%s': non-interactive and no cached tokens found.", server_name)
        port = _configure_callback_port(cfg)
        client_metadata = _build_client_metadata(cfg, port)
        _maybe_preregister_client(storage, cfg, client_metadata, port)
        return SPIRITAGENT_PROVIDER_CLS(
            server_name=server_name,
            server_url=entry.server_url,
            client_metadata=client_metadata,
            storage=storage,
            redirect_handler=_make_redirect_handler(port),
            callback_handler=_make_wait_for_callback(port),
            timeout=float(cfg.get("timeout", 300)),
        )

    def remove(self, server_name: str) -> None:
        with self._entries_lock:
            self._entries.pop(server_name, None)
        remove_oauth_tokens(server_name)
        logger.info("MCP OAuth '%s': evicted from cache and removed from disk", server_name)

    async def invalidate_if_disk_changed(self, server_name: str) -> bool:
        if not (entry := self._entries.get(server_name)) or not entry.provider:
            return False
        async with entry.lock:
            tokens_path = _get_token_dir() / f"{_safe_filename(server_name)}.json"
            try:
                mtime_ns = tokens_path.stat().st_mtime_ns
            except (FileNotFoundError, OSError):
                return False
            if mtime_ns != entry.last_mtime_ns:
                entry.last_mtime_ns = mtime_ns
                if hasattr(entry.provider, "_initialized"):
                    entry.provider._initialized = False
                logger.info("MCP OAuth '%s': tokens file changed, forcing reload", server_name)
                return True
            return False

    async def handle_401(self, server_name: str, failed_access_token: str | None = None) -> bool:
        if not (entry := self._entries.get(server_name)) or not entry.provider:
            return False
        key = failed_access_token or "<unknown>"
        loop = asyncio.get_running_loop()
        async with entry.lock:
            if not (pending := entry.pending_401.get(key)):
                pending = loop.create_future()
                entry.pending_401[key] = pending

                async def _do_handle() -> None:
                    try:
                        if await self.invalidate_if_disk_changed(server_name):
                            if not pending.done():
                                pending.set_result(True)
                            return
                        ctx = getattr(entry.provider, "context", None)
                        can_refresh = bool(ctx.can_refresh_token()) if ctx and hasattr(ctx, "can_refresh_token") else False
                        if not pending.done():
                            pending.set_result(can_refresh)
                    except Exception as exc:
                        logger.warning("MCP OAuth '%s': 401 handler failed: %s", server_name, exc)
                        if not pending.done():
                            pending.set_result(False)
                    finally:
                        entry.pending_401.pop(key, None)

                t = asyncio.create_task(_do_handle())
                self._bg_tasks.add(t)
                t.add_done_callback(self._bg_tasks.discard)
        try:
            return await pending
        except Exception as exc:
            logger.warning("MCP OAuth '%s': awaiting 401 handler failed: %s", server_name, exc)
            return False


_MANAGER: MCPOAuthManager | None = None
_MANAGER_LOCK = threading.Lock()


def get_manager() -> MCPOAuthManager:
    """返回进程内的 MCPOAuthManager 单例（懒加载）。"""
    global _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is None:
            _MANAGER = MCPOAuthManager()
        return _MANAGER


def reset_manager_for_tests() -> None:
    """重置全局 MCPOAuthManager 单例，仅供测试使用。"""
    global _MANAGER
    with _MANAGER_LOCK:
        _MANAGER = None
