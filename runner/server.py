import argparse
import asyncio
import json
import logging
import platform
import sys
import threading
import time
import uuid

import websockets
from runner_version import __version__
from tools import discover_builtin_tools
from tools import registry
from tools import ToolError
from tools.files.file_tools import reset_max_read_chars_cache
from tools.interrupt import set_global_interrupt
from tools.interrupt import set_interrupt
from tools.mcp import discover_mcp_tools
from tools.mcp.mcp_tool import reload_mcp_servers
from tools.tool_output_limits import reset_cache as reset_output_limits_cache
from tools.toolsets import get_disabled_toolset_ids
from utils import pid_exists
from utils import set_handler
from utils.capabilities import disk_free_bytes
from utils.capabilities import network_reachable
from utils.capabilities import snapshot
from utils.constants import get_deskagent_home

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("deskagent_runner")

_ACTIVE_WS = None
_RUNNER_LOOP: asyncio.AbstractEventLoop | None = None
_PENDING_RPC: dict[str, asyncio.Future] = {}
# Process-scoped: MCP tool cache lives for the runner's lifetime, so a single
# discovery on the first connect is enough.
_discovery_started = False
_STARTED_AT = time.time()
_RECONNECT_COUNT = 0


async def _send(ws, req_id, **fields):
    await ws.send(json.dumps({"jsonrpc": "2.0", "id": req_id, **fields}))


async def _send_notification(ws, method, params, id=None):
    body = {"jsonrpc": "2.0", "method": method, "params": params}
    if id is not None:
        body["id"] = id
    await ws.send(json.dumps(body))


async def request_llm_from_desktop(kwargs: dict) -> str:
    """Send a ``request_llm`` to Desktop and return the raw LLM response text.

    Used by tools that need a one-off LLM completion (vision, web
    extraction). All network auth lives in Desktop; the runner has no
    provider credentials.

    The Backend returns ``{ "content": str, "usage": dict|null }``; we
    extract and return only the content string so callers get plain text.
    A non-dict response, or a dict that does not carry a ``content`` key,
    is rejected as a protocol error — callers depend on the ``str`` contract.
    """
    if not _ACTIVE_WS:
        raise RuntimeError("No active WebSocket connection")

    req_id = f"req_llm_{uuid.uuid4().hex[:8]}"
    fut = asyncio.Future()
    _PENDING_RPC[req_id] = fut

    await _send_notification(_ACTIVE_WS, "request_llm", kwargs, id=req_id)

    # Honor the caller's per-call timeout when one is supplied. Floor at
    # 1.0s to avoid pathological zero-second waits; no upper bound, since
    # the caller's budget is the right place to enforce a cap.
    try:
        timeout_s = float(kwargs.get("timeout", 120.0))
    except (TypeError, ValueError):
        timeout_s = 120.0
    timeout_s = max(timeout_s, 1.0)

    try:
        result = await asyncio.wait_for(fut, timeout=timeout_s)
    finally:
        _PENDING_RPC.pop(req_id, None)

    if isinstance(result, dict):
        if "error" in result and isinstance(result["error"], dict):
            reason = result["error"].get("reason", "unknown")
            raise RuntimeError(f"request_llm: backend error ({reason}): {result['error'].get('message', result['error'])}")
        return _extract_llm_content(result)
    if isinstance(result, str):
        return result
    raise RuntimeError(f"request_llm: backend returned {type(result).__name__}, expected str or dict with content")


def _extract_llm_content(result: dict) -> str:
    """Pull text out of a Backend completion response.

    The Backend ``/api/llm/completion`` proxy returns either
    ``{"content": str, "usage": ...}`` (legacy normalized form) or a raw
    OpenAI-compatible ``{"choices": [{"message": {"content": str}}], ...}``
    body. Some providers wrap text in a list of content blocks
    (``{"choices": [{"message": {"content": [{"type": "text", "text": "..."}]}}]}``).
    Returns the extracted text or ``""`` if no text-bearing key is found,
    so callers can degrade gracefully instead of failing the whole tool call.
    """
    if "content" in result and isinstance(result["content"], str):
        return result["content"]
    if "text" in result and isinstance(result["text"], str):
        return result["text"]
    choices = result.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if isinstance(message, dict):
            inner = message.get("content")
            if isinstance(inner, str):
                return inner
            if isinstance(inner, list):
                parts = []
                for block in inner:
                    if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                        parts.append(block["text"])
                if parts:
                    return "".join(parts)
    return ""


async def process_request(ws, req):
    req_id = req.get("id")
    method = req.get("method")
    params = req.get("params", {})

    # Notifications carry no id; drop early so the liveness ping doesn't spam -32601 log noise.
    if req_id is None and method:
        return

    # A new request just arrived. Clear any stale per-thread interrupt
    # left over from a previous request so the current request's tools
    # don't immediately bail.  For cancel requests we also set the
    # cross-thread ``_global_interrupt`` so in-flight tool handlers
    # from *other* requests see the flag on their next ``is_interrupted()``
    # check.  Non-cancel requests clear the global flag left over from
    # a *prior* cancel — the flag persists across requests so in-flight
    # workers spawned before the cancel can still observe it; it is
    # cleared here on the *next* request rather than in the cancel's
    # own finally-block, which runs before those workers poll.
    if method == "deskagent.cancel":
        set_global_interrupt(True)
    else:
        set_global_interrupt(False)
    set_interrupt(False, thread_id=None)
    try:
        if method == "deskagent.cancel":
            await _send(ws, req_id, result={"ok": True})
            return

        if method == "get_tools":
            await _send(
                ws,
                req_id,
                result={"tools": registry.get_schemas_for_llm(get_disabled_toolset_ids())},
            )
            return

        if method == "deskagent.info":
            await _send(ws, req_id, result=_build_info())
            return

        if method == "mcp.reload":
            # Clear config caches so tool_output_limits and file_read_max_chars
            # pick up any config.yaml changes without requiring a runner restart.
            reset_output_limits_cache()
            reset_max_read_chars_cache()
            # Off-load: reload_mcp_servers joins the MCP loop thread and waits
            # up to 15s on shutdown — running it inline would block the
            # Runner's WS event loop and starve deskagent.cancel / heartbeats.
            result = await asyncio.to_thread(reload_mcp_servers)
            await _send(ws, req_id, result=result)
            return

        if method == "execute_tool":
            name = params.get("name")
            if not name:
                raise ValueError("Missing 'name' in params")
            try:
                result = await registry.async_dispatch(name, params.get("args", {}))
            except ToolError as e:
                await _send(ws, req_id, error={"code": -32000, "message": str(e)})
                return
            await _send(ws, req_id, result=result)
            return

        await _send(ws, req_id, error={"code": -32601, "message": "Method not found"})
    except Exception as e:
        await _send(ws, req_id, error={"code": -32000, "message": str(e)})


MAX_RECONNECT_ATTEMPTS = 15
BASE_BACKOFF_S = 2.0
MAX_BACKOFF_S = 30.0

_BG_TASKS: set[asyncio.Task] = set()


async def runner_loop(ws_url: str) -> None:
    """Long-running connection loop for Desktop WS.

    Keeps a persistent connection, handles reconnect backoff, dispatches
    RPC calls, and notifies Desktop when ready.
    """
    global _ACTIVE_WS, _RUNNER_LOOP, _RECONNECT_COUNT

    current_url = ws_url
    attempt = 0

    while True:
        logger.info(f"Connecting to Desktop WS: {current_url} (attempt {attempt + 1})")
        cancelled = False
        # P1-3 (runtime audit): proactively drain any stale
        # ``_PENDING_RPC`` futures before the new connection opens. The
        # ``finally`` block on the previous connection does this too,
        # but only after the websocket context manager fully unwinds —
        # any future created in the gap would leak an awaited
        # ``wait_for`` on the caller side. Cheap insurance: set
        # ConnectionError on every future that's still pending.
        for _fut in list(_PENDING_RPC.values()):
            if not _fut.done():
                _fut.set_exception(ConnectionError("Runner WS reconnecting; abandoning in-flight request"))
        _PENDING_RPC.clear()
        try:
            async with websockets.connect(current_url) as ws:
                _ACTIVE_WS = ws
                _RUNNER_LOOP = asyncio.get_running_loop()
                attempt = 0  # reset on successful connection
                try:
                    await _send_notification(ws, "runner_ready", _runner_ready_payload())
                    _schedule_background_mcp_discovery()

                    async for message in ws:
                        try:
                            data = json.loads(message)
                        except json.JSONDecodeError:
                            logger.error("Invalid JSON received")
                            continue
                        # JSON-RPC frames must be JSON objects. A frame
                        # that parses to ``int``/``list``/``None`` is
                        # a malformed message — log and skip rather
                        # than letting ``data.get(...)`` raise and
                        # kill the reader task.
                        if not isinstance(data, dict):
                            logger.error("Non-object JSON frame received: %r", type(data).__name__)
                            continue

                        req_id = data.get("id")
                        if req_id and (fut := _PENDING_RPC.pop(req_id, None)):
                            if "error" in data:
                                err = data["error"]
                                err_msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                                fut.set_exception(Exception(err_msg))
                            else:
                                fut.set_result(data.get("result"))
                            continue

                        t = asyncio.create_task(process_request(ws, data))
                        _BG_TASKS.add(t)
                        t.add_done_callback(_BG_TASKS.discard)
                finally:
                    _ACTIVE_WS = None
                    _RUNNER_LOOP = None
                    # Drain pending request_llm futures with ConnectionError so
                    # callers' wait_for returns fast; set_exception (not cancel)
                    # preserves the "disconnected" reason for the LLM.
                    for _fut in list(_PENDING_RPC.values()):
                        # A future may be done (response already popped, or
                        # wait_for cancelled it) between the values() snapshot
                        # and the iteration; set_exception on a done future
                        # raises InvalidStateError and would break the loop.
                        if not _fut.done():
                            _fut.set_exception(ConnectionError("Runner WS disconnected before response arrived"))
                    _PENDING_RPC.clear()

        except websockets.exceptions.ConnectionClosed:
            logger.warning("WebSocket connection closed by Desktop.")
        except asyncio.CancelledError:
            # Test cleanup / normal shutdown — exit the loop. Don't bump
            # ``_RECONNECT_COUNT`` because we intentionally tore down, not
            # dropped mid-session.
            cancelled = True
        except Exception as e:
            logger.error(f"WebSocket error: {e}")

        if cancelled:
            break
        _RECONNECT_COUNT += 1
        attempt += 1
        if attempt >= MAX_RECONNECT_ATTEMPTS:
            logger.error(f"Failed to reconnect after {MAX_RECONNECT_ATTEMPTS} attempts. Exiting.")
            sys.exit(1)

        # Try to read a fresh endpoint from the file Desktop writes.
        new_url = _read_endpoint_url()
        if new_url:
            current_url = new_url

        backoff = min(BASE_BACKOFF_S * (2 ** min(attempt - 1, 4)), MAX_BACKOFF_S)
        logger.info(f"Reconnecting in {backoff:.1f}s (attempt {attempt}/{MAX_RECONNECT_ATTEMPTS})")
        await asyncio.sleep(backoff)


def _read_endpoint_url() -> str | None:
    """Read ``$DESKAGENT_HOME/desktop-endpoint.json`` and return a WS URL.

    Returns ``None`` if the file is missing, malformed, or the Desktop PID
    is no longer alive (stale file from a crashed Desktop).
    """
    try:
        endpoint_path = get_deskagent_home() / "desktop-endpoint.json"
        if not endpoint_path.exists():
            return None
        data = json.loads(endpoint_path.read_text(encoding="utf-8"))
        port = data.get("port")
        pid = data.get("pid")
        if not isinstance(port, int) or port <= 0:
            return None
        # Skip stale files left by a crashed Desktop. pid_exists() over
        # os.kill(pid, 0): latter is unsafe on Windows (bpo-14484).
        if isinstance(pid, int) and pid > 0:
            if not pid_exists(pid):
                logger.debug(f"Desktop PID {pid} is gone, ignoring stale endpoint file")
                return None
        return f"ws://127.0.0.1:{port}/rpc"
    except Exception:
        return None


def _runner_ready_payload() -> dict:
    """Compact handshake payload sent right after WS connect.

    Mirrors ``deskagent.info`` minus fields that depend on runtime stats
    (uptime, reconnect count) — capabilities are computed once at handshake
    time and cached. Desktop uses this to decide whether to expose features
    that depend on optional OS subsystems (microphone, screen capture,
    system activity signals, local STT/TTS).

    On probe failure we surface a structurally distinct shape so the
    Desktop can tell ``probe_failed=True`` apart from
    ``capabilities={...all False}``. The Desktop should treat any
    feature whose value is missing from the dict as "do not enable"
    regardless of the failure flag.
    """
    payload: dict[str, object] = {
        "version": __version__,
        "capabilities": {},
        "probe_failed": False,
    }
    try:
        caps = snapshot()
        if isinstance(caps, dict):
            payload["capabilities"] = caps
        else:
            payload["probe_failed"] = True
    except Exception as e:
        logger.warning(f"capabilities probe failed: {e}")
        payload["probe_failed"] = True
    # P0-1 (runtime audit): \`bundled_voice_ids\` was a dead field —
    # the desktop never consumed it. Drop it from the wire payload
    # so we don't ship unused bytes on every WS open. A future
    # consumer (e.g. pre-rendering the local voice picker) can
    # add a dedicated \`runner.bundled_voices\` RPC.
    return payload


def _build_info() -> dict:
    """Full snapshot returned by the ``deskagent.info`` RPC.

    Captures process / OS state in addition to capabilities so the
    Desktop diagnostic panel can tell stale-process from cold-start, and
    so the agent itself can adapt behaviour (e.g. avoid heavy tools when
    disk is tight).
    """
    try:
        caps = snapshot()
    except Exception:
        caps = {}
    try:
        mcp_servers = sorted(_active_mcp_server_names())
    except Exception:
        mcp_servers = []
    try:
        tool_names = registry.get_all_tool_names()
    except Exception:
        tool_names = []
    return {
        "version": __version__,
        "started_at": _STARTED_AT,
        "uptime_seconds": round(time.time() - _STARTED_AT, 2),
        "reconnect_count": _RECONNECT_COUNT,
        "capabilities": caps,
        "system": {
            "platform": sys.platform,
            "python": sys.version.split()[0],
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "tool_count": len(tool_names),
        "mcp_servers": mcp_servers,
        "network_reachable": network_reachable(),
        "disk_free_bytes": disk_free_bytes(get_deskagent_home()),
    }


def _active_mcp_server_names() -> list[str]:
    """Names of MCP servers with at least one live task in ``mcp_tool._servers``.

    Resolved via the same MCP module loader the rest of the runner uses,
    but never raises — a stale registry state during shutdown must not
    break ``deskagent.info``.
    """
    try:
        from tools.mcp import mcp_tool

        servers = getattr(mcp_tool, "_servers", None)
        if not servers:
            return []
        return sorted(servers.keys())
    except Exception:
        return []


def main():
    parser = argparse.ArgumentParser(description="DeskAgent Runner Server")
    parser.add_argument("--desktop-ws", required=True, help="Desktop WebSocket URL (e.g. ws://127.0.0.1:8080/rpc)")
    args = parser.parse_args()

    discover_builtin_tools()
    set_handler(request_llm_from_desktop)

    try:
        asyncio.run(runner_loop(args.desktop_ws))
    except KeyboardInterrupt:
        logger.info("Runner stopped.")


def _schedule_background_mcp_discovery():
    """Run ``discover_mcp_tools()`` off the WS event loop. Process-scoped (no-op on reconnect) — concurrent discovery threads would race on the shared registry during the 60-120s stdio spawn window."""
    global _discovery_started
    if _discovery_started:
        return
    _discovery_started = True

    def _run():
        try:
            discover_mcp_tools()
        except Exception as e:
            logger.warning(f"MCP discovery in background failed: {e}")
        finally:
            _notify_tools_changed()

    threading.Thread(target=_run, daemon=True).start()


def _notify_tools_changed():
    """Send `tools_changed` notification to the desktop via the active WS.

    No-op when the WS isn't yet connected (e.g. shutdown raced ahead of
    discovery) or has been closed.
    """
    ws = _ACTIVE_WS
    loop = _RUNNER_LOOP
    if ws is None or loop is None or loop.is_closed():
        return
    try:
        asyncio.run_coroutine_threadsafe(
            _send_notification(ws, "tools_changed", {}),
            loop,
        )
    except Exception as e:
        logger.warning(f"Failed to dispatch tools_changed notification: {e}")


if __name__ == "__main__":
    main()
