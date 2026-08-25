"""see-through 拆分编排 — HF Space → PSD 资产落盘 → puppet 描述符 manifest。

复用 mesh2d 的行状态机 / outfit 接缝 / WS 事件路径：本模块只产出与
`_run_pipeline_core` 同构的 (manifest_json, layer_entries)，落库由 mesh2d
pipeline 的调用方统一执行；SeeThroughError 时调用方降级 CPU mesh2d 链。"""

import json

from components import get_logger

from .. import asset_store
from .client import split_to_psd

logger = get_logger(__name__)

PSD_MANIFEST_SCHEMA = "spiritagent.2d.psd/1"


async def run_seethrough_split(user_id: int, image_bytes: bytes) -> tuple[str, list[dict[str, str]]]:
    """拆分并把 PSD 存资产库；返回 (manifest_json, [{"name": "psd", "url": 裸路径}])。"""
    psd_bytes = await split_to_psd(image_bytes)
    psd_path = asset_store.save_companion_asset(psd_bytes, user_id=user_id, label="2d_psd", ext="psd")
    manifest_json = json.dumps({"schema": PSD_MANIFEST_SCHEMA, "kind": "psd", "psd": psd_path}, ensure_ascii=False)
    logger.info("see-through split done", extra={"user_id": user_id, "psd_bytes": len(psd_bytes)})
    return manifest_json, [{"name": "psd", "url": psd_path}]
