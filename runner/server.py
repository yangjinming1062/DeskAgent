import argparse
import asyncio
import contextlib
import json
import logging
import platform
import sys
import threading
import time
import uuid
from typing import Any

import websockets

import utils.credential_files
import utils.env_passthrough
from runner_version import __version__
from tools import ToolError, discover_builtin_tools, registry
from tools.files import reset_max_read_chars_cache
from tools.mcp import discover_mcp_tools, get_active_mcp_servers, reload_mcp_servers
from tools.tool_output_limits import reset_cache
from tools.toolsets import get_disabled_toolset_ids
from utils import (
    DesktopEndpoint,
    connect_desktop,
    disk_free_bytes,
    get_spiritagent_home,
    init_runner_job_object,
    network_reachable,
    read_endpoint,
    set_global_interrupt,
    set_handler,
    set_inmemory_config,
    set_interrupt,
    set_main_loop,
    snapshot,
    snapshot_health,
)

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("spiritagent_runner")


def _require_supported_host() -> None:
    """Runner 只在 Windows + macOS 上分发; 在其他平台直接拒绝启动 — 跑在不支持的主机上, 多项子系统(麦克风探测、system-activity 后端、cu_* 工具)都会悄悄失效, 不如直接在此硬失败, 给运维/CI 一个明确信号。"""
    if sys.platform not in {"win32", "darwin"}:
        raise SystemExit(f"SpiritAgent Runner does not support the {sys.platform!r} host. Supported hosts are Windows and macOS only.")


_ACTIVE_WS: Any | None = None
_RUNNER_LOOP: asyncio.AbstractEventLoop | None = None
_PENDING_RPC: dict[str, asyncio.Future] = {}
# 进程级: MCP 工具缓存随 runner 生命周期存活, 首次连接做一次发现即可。
_discovery_started = False
_STARTED_AT = time.time()
_RECONNECT_COUNT = 0

# 重连退避 + 端点文件轮询间隔。
MAX_RECONNECT_ATTEMPTS = 15
BASE_BACKOFF_S = 2.0
MAX_BACKOFF_S = 30.0
_ENDPOINT_POLL_S = 1.0

_BG_TASKS: set[asyncio.Task] = set()


async def _send(ws: Any, req_id: Any, **fields: Any) -> None:
    await ws.send(json.dumps({"jsonrpc": "2.0", "id": req_id, **fields}))


async def _send_notification(ws: Any, method: str, params: dict[str, Any], id: Any = None) -> None:
    body: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params}
    if id is not None:
        body["id"] = id
    await ws.send(json.dumps(body))


async def request_llm_from_desktop(kwargs: dict[str, Any]) -> str:
    """向 Desktop 发 ``request_llm`` 并返回原始 LLM 文本响应(供 vision / web_extract 等一次性补全的工具使用; 所有网络鉴权都在 Desktop 侧完成, runner 自身不带凭据)。

    Backend 返回 ``{ "content": str, "usage": dict|null }`` 或原始 OpenAI 兼容体; 这里抽取出文本
    给调用方纯 ``str``。不携带文本字段的 dict 降级为 ``""``(见 ``_extract_llm_content``); 既不是 str 也不是
    dict 的载荷按协议错误拒绝。
    """
    if (ws := _ACTIVE_WS) is None:
        raise RuntimeError("No active WebSocket connection")

    req_id = f"req_llm_{uuid.uuid4().hex[:8]}"
    fut: asyncio.Future = asyncio.Future()
    _PENDING_RPC[req_id] = fut

    await _send_notification(ws, "request_llm", kwargs, id=req_id)

    # 尊重调用方传入的 per-call 超时: 下限设 1.0s 避免出现 0 秒等; 不设上限, 上限由调用方在业务预算处自行约束。
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


def _extract_llm_content(result: dict[str, Any]) -> str:
    """``/api/llm/completion`` 代理返回 ``{"content": str, "usage": ...}``。缺失时返回空串，让调用方优雅处理而非让整个工具调用失败。"""
    content = result.get("content")
    return content if isinstance(content, str) else ""


async def process_request(ws: Any, req: dict[str, Any]) -> None:
    req_id = req.get("id")
    method = req.get("method")
    params = req.get("params", {})

    # notification 不带 id; 提前丢弃, 否则心跳探测会刷出 -32601 log noise。
    if req_id is None and method:
        return

    # 新请求到达时清理上一条请求残留的 per-thread interrupt, 防止当前请求的工具立刻被 bail。
    # 对 cancel 请求置跨线程 ``_global_interrupt``, 让其他请求里正在执行的工具处理器在下一次
    # ``is_interrupted()`` 检查时看到标志。只有下一次 execute_tool 才清除 — 诊断探针
    # (spiritagent.info / get_tools) 不能误吃待发 cancel。
    if method == "spiritagent.cancel":
        set_global_interrupt(True)
    elif method == "execute_tool":
        set_global_interrupt(False)
    set_interrupt(False, thread_id=None)
    try:
        if method == "spiritagent.cancel":
            await _send(ws, req_id, result={"ok": True})
            return

        if method == "get_tools":
            await _send(ws, req_id, result={"tools": registry.get_schemas_for_llm(get_disabled_toolset_ids())})
            return

        if method == "spiritagent.info":
            await _send(ws, req_id, result=await _build_info())
            return

        if method == "spiritagent.config.update":
            # 重置缓存对标 mcp.reload, 让派生限制立刻看到新 config。
            config = params.get("config")
            if not isinstance(config, dict):
                raise ValueError("spiritagent.config.update requires a 'config' object")
            set_inmemory_config(config)
            reset_cache()
            reset_max_read_chars_cache()
            utils.env_passthrough.reset_cache()
            utils.credential_files.reset_cache()
            await _send(ws, req_id, result={"ok": True})
            return

        if method == "mcp.reload":
            # 清掉 config 缓存, 让 tool_output_limits / file_read_max_chars 不需要重启 runner 就能看到改动。
            reset_cache()
            reset_max_read_chars_cache()
            utils.env_passthrough.reset_cache()
            utils.credential_files.reset_cache()
            # 卸载到线程: reload_mcp_servers 会 join MCP loop 线程, 关停最长等 15s — 同步执行会阻塞
            # runner 的 WS event loop, 把 spiritagent.cancel / 心跳掐死。
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
        # 回复本身绝不能再抛: 中途断连的 handler 会让后台任务以未捕获异常死去。
        with contextlib.suppress(Exception):
            await _send(ws, req_id, error={"code": -32000, "message": str(e)})


def _fail_pending_rpcs(reason: str) -> None:
    """对所有 in-flight 的 ``request_llm`` future 抛失败, 让调用方的 ``wait_for`` 迅速返回。

    用 ``set_exception`` 而不是 cancel 是为了把断连原因透给 LLM。future 可能在 values() 快照到迭代之间已经
    done(响应被取走 / ``wait_for`` 被取消), 在已 done 的 future 上 ``set_exception`` 会抛 InvalidStateError。
    """
    for fut in list(_PENDING_RPC.values()):
        if not fut.done():
            fut.set_exception(ConnectionError(reason))
    _PENDING_RPC.clear()


async def runner_loop(endpoint: DesktopEndpoint) -> None:
    """与 Desktop IPC 的长连接主循环: 维护持久连接、处理重连退避、派发 RPC、Ready 时主动通知。"""
    global _ACTIVE_WS, _RUNNER_LOOP, _RECONNECT_COUNT

    current_endpoint = endpoint
    attempt = 0

    while True:
        cancelled = False
        # 等 Desktop 发布 endpoint(重启窗口、文件缺失/陈旧)是正常稳态, 不是连接失败 — 不能消耗下面的重连预算。
        tried_connect = current_endpoint is not None
        if current_endpoint is None:
            logger.info("Desktop endpoint not ready, waiting for endpoint file...")
        else:
            logger.info(f"Connecting to Desktop IPC: {current_endpoint.path} (attempt {attempt + 1})")
            # 新连接打开前主动 drain 掉残留的 ``_PENDING_RPC`` future。前一条连接的 ``finally`` 也会做,
            # 但要等 websocket 上下文完全退出之后 — 那窗口里新建的 future 会让调用方 ``wait_for`` 泄漏。
            # 廉价保险: 给所有尚未 done 的 future 抛 ConnectionError。
            _fail_pending_rpcs("Runner WS reconnecting; abandoning in-flight request")
            try:
                connection = await connect_desktop(current_endpoint)
                async with connection as ws:
                    _ACTIVE_WS = ws
                    _RUNNER_LOOP = asyncio.get_running_loop()
                    set_main_loop(_RUNNER_LOOP)
                    attempt = 0  # 连接成功后重置
                    try:
                        await _send_notification(ws, "runner_ready", await _runner_ready_payload())
                        _schedule_background_mcp_discovery()

                        async for message in ws:
                            try:
                                data = json.loads(message)
                            except json.JSONDecodeError:
                                logger.error("Invalid JSON received")
                                continue
                            # JSON-RPC 帧必须是 JSON 对象。解析成 ``int`` / ``list`` / ``None`` 的帧是畸形 — 打 log 跳过, 防止 ``data.get(...)`` 抛异常把读取任务杀掉。
                            if not isinstance(data, dict):
                                logger.error("Non-object JSON frame received: %r", type(data).__name__)
                                continue

                            req_id = data.get("id")
                            if req_id is not None and (fut := _PENDING_RPC.pop(req_id, None)):
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
                        # 排空待发 request_llm future, 让调用方 ``wait_for`` 携带断连原因迅速返回。
                        _fail_pending_rpcs("Runner WS disconnected before response arrived")

            except websockets.exceptions.ConnectionClosed:
                logger.warning("WebSocket connection closed by Desktop.")
            except websockets.exceptions.InvalidStatus as e:
                # Desktop 升级前 token 校验返回 HTTP 401: 本进程仍持有上一个会话的 token(Desktop 重启过)。
                # 丢弃缓存 endpoint, 下一次从 endpoint 文件重读新的路径+token — 重试旧的一定 401 白白烧掉重试预算。
                logger.warning(f"Desktop rejected handshake ({e.response.status_code}); refreshing endpoint")
                current_endpoint = None
            except asyncio.CancelledError:
                # 测试清理 / 正常退出 — 离开循环, 不 bump ``_RECONNECT_COUNT``, 因为这是主动拆除, 不是会话中途掉线。
                cancelled = True
            except Exception as e:
                logger.error(f"WebSocket error: {e}")

        if cancelled:
            break

        # 从 Desktop 写的文件读取最新 endpoint(路径+token); 文件缺失/陈旧就保留缓存的 endpoint。
        new_endpoint = read_endpoint()
        if new_endpoint:
            current_endpoint = new_endpoint

        if not tried_connect:
            await asyncio.sleep(_ENDPOINT_POLL_S)
            continue

        _RECONNECT_COUNT += 1
        attempt += 1
        if attempt >= MAX_RECONNECT_ATTEMPTS:
            logger.error(f"Failed to reconnect after {MAX_RECONNECT_ATTEMPTS} attempts. Exiting.")
            sys.exit(1)

        backoff = min(BASE_BACKOFF_S * (2 ** min(attempt - 1, 4)), MAX_BACKOFF_S)
        logger.info(f"Reconnecting in {backoff:.1f}s (attempt {attempt}/{MAX_RECONNECT_ATTEMPTS})")
        await asyncio.sleep(backoff)


async def _runner_ready_payload() -> dict[str, Any]:
    """编译 ``runner_ready`` 通知负载: Desktop 用它决定要不要暴露依赖可选 OS 子系统的功能(麦克风、截屏、system activity、本地 STT/TTS)。

    探测失败时返回结构上独立于 ``capabilities={...all False}`` 的形态(``probe_failed=True``), 让 Desktop 能区分;
    Desktop 应当把任何 ``capabilities`` 里缺失的键视为"不启用", 不管 ``probe_failed`` 是什么。
    """
    payload: dict[str, Any] = {"version": __version__, "capabilities": {}, "capabilities_health": {}, "probe_failed": False}
    try:
        caps = await asyncio.to_thread(snapshot)
        if isinstance(caps, dict):
            payload["capabilities"] = caps
            try:
                payload["capabilities_health"] = await asyncio.to_thread(snapshot_health)
            except Exception:
                payload["capabilities_health"] = {}
        else:
            payload["probe_failed"] = True
    except Exception as e:
        logger.warning(f"capabilities probe failed: {e}")
        payload["probe_failed"] = True
    return payload


async def _build_info() -> dict[str, Any]:
    """``spiritagent.info`` RPC 的完整快照: 除能力外还含进程 / OS 状态, 让 Desktop 诊断面板分得清"陈旧进程"与"冷启动", 也让 agent 自己能据此调整行为(例如磁盘吃紧时避免重型工具)。"""
    try:
        caps = await asyncio.to_thread(snapshot)
        if not isinstance(caps, dict):
            caps = {}
    except Exception:
        caps = {}
    try:
        health = await asyncio.to_thread(snapshot_health)
        if not isinstance(health, dict):
            health = {}
    except Exception:
        health = {}
    mcp_servers = get_active_mcp_servers()
    try:
        tool_names = registry.get_all_tool_names()
    except Exception:
        tool_names = []
    # ``network_reachable`` 可能阻塞 ~1.5s; 放到线程里避免探测期间心跳 / spiritagent.cancel 失去响应。
    reachable = await asyncio.to_thread(network_reachable)
    return {
        "version": __version__,
        "started_at": _STARTED_AT,
        "uptime_seconds": round(time.time() - _STARTED_AT, 2),
        "reconnect_count": _RECONNECT_COUNT,
        "capabilities": caps,
        "capabilities_health": health,
        "system": {"platform": sys.platform, "python": sys.version.split()[0], "release": platform.release(), "machine": platform.machine()},
        "tool_count": len(tool_names),
        "mcp_servers": mcp_servers,
        "network_reachable": reachable,
        "disk_free_bytes": disk_free_bytes(get_spiritagent_home()),
    }


def main() -> None:
    _require_supported_host()
    init_runner_job_object()
    parser = argparse.ArgumentParser(description="SpiritAgent Runner Server")
    parser.add_argument("--desktop-endpoint", required=True, help="Desktop IPC path (Windows named pipe or Unix socket path)")
    parser.add_argument("--desktop-auth", required=True, help="Desktop handshake token (from --desktop-auth at spawn)")
    args = parser.parse_args()

    transport = "pipe" if sys.platform == "win32" else "unix"
    endpoint = DesktopEndpoint(transport=transport, path=args.desktop_endpoint, token=args.desktop_auth)

    discover_builtin_tools()
    set_handler(request_llm_from_desktop)

    try:
        asyncio.run(runner_loop(endpoint))
    except KeyboardInterrupt:
        logger.info("Runner stopped.")


def _schedule_background_mcp_discovery() -> None:
    """``discover_mcp_tools()`` 跑在 WS event loop 之外; 进程级(重连时不重复) — 并发的发现线程会在 60-120s 的 stdio spawn 窗口里争抢共享 registry。"""
    global _discovery_started
    if _discovery_started:
        return
    _discovery_started = True

    def _run() -> None:
        try:
            discover_mcp_tools()
        except Exception as e:
            logger.warning(f"MCP discovery in background failed: {e}")
        finally:
            _notify_tools_changed()

    threading.Thread(target=_run, daemon=True).start()


def _notify_tools_changed() -> None:
    """通过当前 WS 向 Desktop 发 ``tools_changed``; WS 还没连上时(发现先于连接)或已关闭时 no-op。"""
    ws = _ACTIVE_WS
    loop = _RUNNER_LOOP
    if ws is None or loop is None or loop.is_closed():
        return
    try:
        asyncio.run_coroutine_threadsafe(_send_notification(ws, "tools_changed", {}), loop)
    except Exception as e:
        logger.warning(f"Failed to dispatch tools_changed notification: {e}")


if __name__ == "__main__":
    main()
