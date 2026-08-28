import json
from datetime import datetime
from pathlib import Path
from typing import Any

from components import ensure_utc, get_logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .file_packing import UrlRewriter

logger = get_logger(__name__)

# 导出表清单：与 manifest.tables 一致；insert 端需保证 avatar_assets / companion_outfits
# 先于 companion_2d_models 与 companion_expression_avatars（依赖它们的 FK）。
TABLES: list[str] = [
    "user_model_configs",
    "personas",
    "avatar_assets",
    "companion_outfits",
    "companion_2d_models",
    "companion_3d_models",
    "companion_expressions",
    "companion_expression_avatars",
    "user_settings",
    "cron_jobs",
    "memories",
]

# 唯一 per-user 表：merge 模式跳过以避免冲突且语义不清
UNIQUE_PER_USER_TABLES: frozenset[str] = frozenset({"user_model_configs", "personas"})

_AVATAR_URL_COLUMNS = ("asset_url", "seed_front_2d_url", "seed_front_3d_url", "seed_back_url")
_OUTFIT_URL_COLUMNS = ("fullbody_url",)
_2D_URL_COLUMNS = ("manifest_path",)
_3D_URL_COLUMNS = ("asset_url",)
_EXPRESSION_AVATAR_URL_COLUMNS = ("asset_url",)


def _iso(dt: datetime | None) -> str | None:
    return ensure_utc(dt).isoformat() if dt else None


def _parse_iso(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return ensure_utc(value)
    return ensure_utc(datetime.fromisoformat(value))


async def serialize_rows(db: AsyncSession, table: str, user_id: int) -> list[dict[str, Any]]:
    model_cls, columns, user_filter = _model_for_table(table)
    rows = (await db.execute(select(model_cls).where(user_filter == user_id))).scalars().all()
    return [_row_to_dict(row, columns) for row in rows]


def _row_to_dict(row: Any, columns: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for col in columns:
        val = getattr(row, col, None)
        if isinstance(val, datetime):
            val = _iso(val)
        out[col] = val
    return out


async def insert_rows(
    db: AsyncSession,
    table: str,
    raw_rows: list[dict[str, Any]],
    target_user_id: int,
    rewriter: UrlRewriter,
    id_map: dict[str, dict[int, int]],
    *,
    mode: str,
) -> dict[int, int]:
    if mode == "merge" and table in UNIQUE_PER_USER_TABLES:
        logger.info("backup insert: skip unique-per-user table in merge mode", extra={"table": table})
        return {}

    model_cls, _, _ = _model_for_table(table)
    new_map: dict[int, int] = {}
    for raw in raw_rows:
        instance = _build_instance(model_cls, raw, target_user_id, rewriter, table, id_map)
        db.add(instance)
        await db.flush()
        src_pk = raw.get("id")
        if src_pk is not None:
            new_map[int(src_pk)] = int(instance.id)
    return new_map


def _build_instance(
    model_cls: type,
    raw: dict[str, Any],
    target_user_id: int,
    rewriter: UrlRewriter,
    table: str,
    id_map: dict[str, dict[int, int]],
) -> Any:
    payload = dict(raw)
    payload.pop("id", None)  # 由 DB 自增分配
    payload.pop("user_id", None)  # 永远绑到 target

    for ts_col in ("created_at", "updated_at", "next_run_at", "portrait_confirmed_at"):
        if ts_col in payload:
            payload[ts_col] = _parse_iso(payload[ts_col])

    for col in _url_columns_for_table(table):
        if col in payload:
            payload[col] = rewriter(payload[col])

    # FK 列重写到 target 用户空间的新 id；旧 PK 无映射说明顺序错乱，置 None
    fk_spec: tuple[tuple[str, str], ...] = ()
    if table == "companion_2d_models":
        fk_spec = (("avatar_id", "avatar_assets"), ("outfit_id", "companion_outfits"))
    elif table == "companion_expression_avatars":
        fk_spec = (("avatar_id", "avatar_assets"),)
    for fk_col, ref_table in fk_spec:
        if fk_col not in payload:
            continue
        src = payload.get(fk_col)
        payload[fk_col] = id_map.get(ref_table, {}).get(src) if src is not None else None

    # source_portrait_id：旧 AvatarAsset.id 无意义，置空
    if table == "companion_3d_models":
        payload["source_portrait_id"] = None

    payload["user_id"] = target_user_id
    return model_cls(**payload)


def _url_columns_for_table(table: str) -> tuple[str, ...]:
    return {
        "avatar_assets": _AVATAR_URL_COLUMNS,
        "companion_outfits": _OUTFIT_URL_COLUMNS,
        "companion_2d_models": _2D_URL_COLUMNS,
        "companion_3d_models": _3D_URL_COLUMNS,
        "companion_expression_avatars": _EXPRESSION_AVATAR_URL_COLUMNS,
    }.get(table, ())


def deserialize_rows(extract_root: Path | str) -> dict[str, list[dict[str, Any]]]:
    db_dir = Path(extract_root) / "db"
    out: dict[str, list[dict[str, Any]]] = {}
    for tbl in TABLES:
        path = db_dir / f"{tbl}.json"
        if not path.exists():
            out[tbl] = []
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid {tbl}.json: {exc}") from exc
        rows = payload.get("rows") if isinstance(payload, dict) else None
        out[tbl] = rows if isinstance(rows, list) else []
    return out


_TABLE_MODELS: dict[str, tuple[type, list[str], Any]] = {}


def _model_for_table(table: str) -> tuple[type, list[str], Any]:
    if table in _TABLE_MODELS:
        return _TABLE_MODELS[table]
    if table == "user_model_configs":
        from modules.auth import UserModelConfig

        cols = [
            "id",
            "llm_provider",
            "llm_base_url",
            "llm_api_key",
            "llm_model_name",
            "stt_provider",
            "stt_base_url",
            "stt_api_key",
            "stt_model_name",
            "tts_provider",
            "tts_base_url",
            "tts_api_key",
            "tts_model_name",
            "image_gen_provider",
            "image_gen_base_url",
            "image_gen_api_key",
            "image_gen_model_name",
            "video_gen_provider",
            "video_gen_base_url",
            "video_gen_api_key",
            "video_gen_model_name",
            "provider_config",
        ]
        model = UserModelConfig
    elif table == "personas":
        from modules.companion import Persona

        cols = ["id", "definition_json", "personality_tags_json", "system_prompt_extras", "is_complete", "is_portrait_confirmed", "portrait_confirmed_at", "render_mode"]
        model = Persona
    elif table == "avatar_assets":
        from modules.companion import AvatarAsset

        cols = ["id", "asset_url", "seed_front_2d_url", "seed_front_3d_url", "seed_back_url", "prompt_json", "style", "seed", "active", "created_at"]
        model = AvatarAsset
    elif table == "companion_outfits":
        from modules.companion import CompanionOutfit

        cols = ["id", "name", "description", "fullbody_url", "style", "status", "source_json", "active", "pending_wear"]
        model = CompanionOutfit
    elif table == "companion_2d_models":
        from modules.companion import Companion2DModel

        cols = ["id", "avatar_id", "outfit_id", "style", "status", "manifest_json", "manifest_path", "layers_json", "content_hash", "active", "error", "priority"]
        model = Companion2DModel
    elif table == "companion_3d_models":
        from modules.companion import Companion3DModel

        cols = [
            "id",
            "asset_url",
            "source_portrait_id",
            "provider",
            "species",
            "rig_type",
            "rig_naming",
            "style",
            "status",
            "has_rig",
            "clip_map_json",
            "provider_phase",
            "content_hash",
            "error",
            "active",
            "provider_task_id",
            "download_urls_json",
        ]
        model = Companion3DModel
    elif table == "companion_expressions":
        from modules.companion import CompanionExpression

        cols = ["id", "name", "label", "valence", "description", "icon", "tags_json"]
        model = CompanionExpression
    elif table == "companion_expression_avatars":
        from modules.companion import CompanionExpressionAvatar

        cols = ["id", "name", "avatar_id", "prompt", "asset_url", "content_hash"]
        model = CompanionExpressionAvatar
    elif table == "user_settings":
        from modules.settings import UserSetting

        cols = ["id", "setting_key", "setting_value"]
        model = UserSetting
    elif table == "cron_jobs":
        from modules.scheduler import CronJob

        cols = ["id", "name", "schedule", "prompt", "deliver", "is_paused", "one_shot", "next_run_at", "created_at"]
        model = CronJob
    elif table == "memories":
        from modules.memory import Memory

        cols = ["id", "content", "context", "tags", "importance", "embedding"]
        model = Memory
    else:
        raise ValueError(f"Unknown table: {table}")

    user_filter = getattr(model, "user_id", None)
    if user_filter is None:
        raise ValueError(f"Table {table} has no user_id column")

    entry = (model, cols, user_filter)
    _TABLE_MODELS[table] = entry
    return entry
