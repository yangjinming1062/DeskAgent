"""see-through HF Space 客户端 — 单图拆分为分层 PSD（Gradio 协议：upload → call → SSE 轮询）。

免费 Space 无 SLA 且有每日额度，失败/超时统一抛 SeeThroughError，由调用方降级 CPU mesh2d 链。"""

import json
from typing import Any

import httpx
from components import SETTINGS, get_logger

logger = get_logger(__name__)

_TOTAL_TIMEOUT_SECONDS = 900.0
_MIN_PSD_BYTES = 10240


class SeeThroughError(RuntimeError):
    """HF Space 拆分失败 — 调用方应降级 CPU mesh2d 链。"""


async def split_to_psd(
    image_bytes: bytes,
    *,
    mime: str = "image/jpeg",
    resolution: int = 1024,
    seed: int = 0,
    tblr_split: bool = True,
) -> bytes:
    """提交拆分并阻塞至完成；返回 PSD 字节。"""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(_TOTAL_TIMEOUT_SECONDS, connect=30.0)) as client:
            file_data = await _upload(client, image_bytes, mime)
            event_id = await _submit(client, file_data, resolution, seed, tblr_split)
            psd_url = await _wait_complete(client, event_id)
            return await _download(client, psd_url)
    except SeeThroughError:
        raise
    except Exception as exc:
        raise SeeThroughError(f"see-through transport failed: {exc}") from exc


async def _upload(client: httpx.AsyncClient, image_bytes: bytes, mime: str) -> dict[str, Any]:
    ext = "png" if "png" in mime else "jpg"
    resp = await client.post(f"{SETTINGS.seethrough_space_base}/upload", files={"files": (f"seed.{ext}", image_bytes, mime)})

    resp.raise_for_status()
    paths = resp.json()

    if not isinstance(paths, list) or not paths:
        raise SeeThroughError("upload returned no file handle")

    # 参数必须传 FileData 对象而非裸路径字符串——后者被新版 Gradio 拒收（event:error null）
    return {"path": paths[0], "meta": {"_type": "gradio.FileData"}}


async def _submit(
    client: httpx.AsyncClient,
    file_data: dict[str, Any],
    resolution: int,
    seed: int,
    tblr_split: bool,
) -> str:
    resp = await client.post(
        f"{SETTINGS.seethrough_space_base}/call/inference",
        json={"data": [file_data, resolution, seed, tblr_split]},
    )
    resp.raise_for_status()
    event_id = resp.json().get("event_id")

    if not event_id:
        raise SeeThroughError("submit returned no event_id")

    return event_id


async def _wait_complete(client: httpx.AsyncClient, event_id: str) -> str:
    """SSE 轮询直到 complete 事件；error 事件与流中断都转 SeeThroughError。"""
    event = ""

    async with client.stream("GET", f"{SETTINGS.seethrough_space_base}/call/inference/{event_id}") as stream:
        async for line in stream.aiter_lines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:") and event:
                data = line[5:].strip()

                if event == "error":
                    raise SeeThroughError(f"space reported error: {data[:200]}")

                if event == "complete":
                    return _extract_psd_url(data)

    raise SeeThroughError("sse stream ended without complete event")


def _extract_psd_url(data: str) -> str:
    payload = json.loads(data)
    first = payload[0] if isinstance(payload, list) and payload else None
    url = first.get("url") if isinstance(first, dict) else None

    if not url:
        raise SeeThroughError("complete payload carries no psd url")

    return url


async def _download(client: httpx.AsyncClient, url: str) -> bytes:
    resp = await client.get(url, follow_redirects=True)
    resp.raise_for_status()

    if len(resp.content) < _MIN_PSD_BYTES:
        raise SeeThroughError(f"psd suspiciously small: {len(resp.content)} bytes")

    return resp.content
