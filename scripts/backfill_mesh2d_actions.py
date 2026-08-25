#!/usr/bin/env python3
"""存量 2D 模型的动作表回填（一次性运维）。

动作系统升级（关键帧 tracks / click / point / idle 扩充）只影响新生成模型的 manifest；
本脚本把最新 DEFAULT_ANIMATIONS 重新烘焙进所有 active 模型的 manifest_json 与资产文件，
并更新 content_hash 与 schema 版本号。骨架 / 图层 / 画布保持不动——旧模型没有 leg 层，
回填后的 locomotion 仍是复合躯干方案。客户端在下次 hydrate 时按新 content_hash 重新拉取。

用法（仓库根目录，需要 backend 的环境变量可用）：
    uv run scripts/backfill_mesh2d_actions.py --dry-run
    uv run scripts/backfill_mesh2d_actions.py
"""

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

from components import session_scope  # noqa: E402
from modules.companion.models import Companion2DModel  # noqa: E402
from services.companion import asset_store  # noqa: E402
from services.companion.mesh2d.manifest_exporter import DEFAULT_ANIMATIONS  # noqa: E402
from sqlalchemy import select  # noqa: E402


async def backfill(dry_run: bool) -> int:
    updated = 0

    async with session_scope() as db:
        rows = (await db.execute(select(Companion2DModel).where(Companion2DModel.active.is_(True)))).scalars().all()

        for model in rows:
            if not model.manifest_json:
                continue

            manifest = json.loads(model.manifest_json)
            manifest["$schema"] = "spiritagent.2d/3"
            manifest["version"] = 3
            manifest["animations"] = DEFAULT_ANIMATIONS
            manifest_json = json.dumps(manifest, ensure_ascii=False, separators=(",", ":"))
            new_hash = hashlib.sha256(manifest_json.encode("utf-8")).hexdigest()

            if new_hash == model.content_hash:
                continue

            print(f"model {model.id} (user {model.user_id}): animations -> v3 keyframe tracks")
            updated += 1

            if dry_run:
                continue

            model.manifest_json = manifest_json
            model.content_hash = new_hash
            model.manifest_path = asset_store.save_companion_asset(
                manifest_json.encode("utf-8"),
                user_id=model.user_id,
                label=f"2d_manifest_{model.id}",
                ext="json",
            )

        if not dry_run:
            await db.commit()

    return updated


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="只打印将回填的模型，不写库不写文件")
    args = parser.parse_args()
    count = asyncio.run(backfill(args.dry_run))
    print(f"done: {count} model(s){' (dry-run)' if args.dry_run else ''}")


if __name__ == "__main__":
    main()
