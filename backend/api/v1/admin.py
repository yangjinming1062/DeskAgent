import asyncio
import contextlib
import json
import os
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from common import get_or_404, get_router, list_response
from components import SETTINGS, apply_partial, get_db, get_logger
from fastapi import Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from modules.auth import (
    User,
    UserCreate,
    UserListResponse,
    UserModelConfig,
    UserModelConfigListItem,
    UserModelConfigListResponse,
    UserModelConfigRequest,
    UserResponse,
    UserUpdate,
    decode_activation_code,
    encode_activation_code,
    generate_activation_token,
    get_current_admin_token,
    hash_activation_token,
    public_provider_slots,
)
from modules.companion import AvatarAsset, Companion3DModel
from modules.system import MessageResponse
from services.companion import delete_portrait_file
from services.gateway import discard_user_session
from services.llm import merge_provider_json
from services.tools import REGISTRY
from services.user_backup import (
    TABLES,
    build_manifest,
    clear_user_scoped_rows,
    collect_files_for_export,
    deserialize_rows,
    insert_rows,
    load_manifest,
    restore_files,
    serialize_rows,
)
from services.ws import MANAGER, cancel_user_cron_turns, discard_user
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

logger = get_logger(__name__)

router = get_router()

# 用户备份 zip 大小上限 500 MB；上传也按此截断以免 OOM。
ARCHIVE_MAX_BYTES = 500 * 1024 * 1024
# 1 MB 分块上传，匹配 update.py 的 CHUNK_SIZE 数量级。
ARCHIVE_UPLOAD_CHUNK_BYTES = 1024 * 1024

# 插入顺序：avatar / outfit 先于依赖它们的 2D 模型。
INSERT_ORDER = (
    "user_model_configs",
    "personas",
    "avatar_assets",
    "companion_outfits",
    "companion_3d_models",
    "companion_2d_models",
    "companion_expressions",
    "user_settings",
    "cron_jobs",
    "memories",
)


@router.get("/users", response_model=UserListResponse)
async def list_users(_admin: str = Depends(get_current_admin_token), db: AsyncSession = Depends(get_db)) -> UserListResponse:
    return list_response((await db.execute(select(User).order_by(User.id))).scalars().all(), UserResponse, UserListResponse)


@router.get("/users/{user_id}", response_model=UserResponse)
async def get_user(user_id: int, _admin: str = Depends(get_current_admin_token), db: AsyncSession = Depends(get_db)) -> UserResponse:
    return UserResponse.model_validate(await get_or_404(db, User, id=user_id, detail="用户不存在。"))


@router.post("/users", response_model=UserResponse)
async def create_user(payload: UserCreate, _admin: str = Depends(get_current_admin_token), db: AsyncSession = Depends(get_db)) -> UserResponse:
    if (await db.execute(select(User).where(User.username == payload.username))).scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="用户名已存在。")
    raw_token = generate_activation_token()
    code = encode_activation_code(payload.base_url, raw_token)
    user = User(username=payload.username, activation_code=code, activation_token_hash=hash_activation_token(raw_token), nightly_activity_enabled=payload.nightly_activity_enabled)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return UserResponse.model_validate(user)


@router.patch("/users/{user_id}", response_model=UserResponse)
async def update_user(user_id: int, payload: UserUpdate, _admin: str = Depends(get_current_admin_token), db: AsyncSession = Depends(get_db)) -> UserResponse:
    user = await get_or_404(db, User, id=user_id, detail="用户不存在。")
    if payload.regenerate_token:
        raw_token = generate_activation_token()
        user.activation_token_hash = hash_activation_token(raw_token)
        base_url = payload.base_url
        if not base_url and user.activation_code:
            try:
                base_url = decode_activation_code(user.activation_code)[0]
            except Exception:
                base_url = "http://localhost:10620"
        user.activation_code = encode_activation_code(base_url or "http://localhost:10620", raw_token)
    elif payload.base_url:
        if user.activation_code:
            try:
                _, token = decode_activation_code(user.activation_code)
                user.activation_code = encode_activation_code(payload.base_url, token)
            except Exception:
                # activation_code 解码失败说明 token 已损坏：不能再以旧 code 当 fallback 让客户端连到老 host。
                # 行为对齐 regenerate_token 分支：默认 base_url 重发一个 token，渲染端能拿到新激活链接。
                raw_token = generate_activation_token()
                user.activation_token_hash = hash_activation_token(raw_token)
                user.activation_code = encode_activation_code(payload.base_url, raw_token)
    apply_partial(user, payload, exclude={"regenerate_token", "base_url"})
    await db.commit()
    await db.refresh(user)
    return UserResponse.model_validate(user)


@router.delete("/users/{user_id}", response_model=MessageResponse)
async def delete_user(user_id: int, _admin: str = Depends(get_current_admin_token), db: AsyncSession = Depends(get_db)) -> MessageResponse:
    await get_or_404(db, User, id=user_id, detail="用户不存在。")
    # 主动踢掉活动 WS，让被删除用户的 renderer 干净断开。
    ws = MANAGER.active_connections.get(user_id)
    if ws is not None:
        with contextlib.suppress(Exception):
            await ws.close(code=1000)
        MANAGER.disconnect(ws, user_id)
    cancel_user_cron_turns(user_id)
    await MANAGER.aunregister_dispatcher(user_id)
    REGISTRY.clear_runner_tools(user_id)
    cancelled_tasks = discard_user_session(user_id)
    discard_user(user_id)
    # 等取消走完再删 DB 行——避免 task 仍在写行 / 持有 DB session / 打开文件句柄时 row 已消失。
    if cancelled_tasks:
        await asyncio.gather(*cancelled_tasks, return_exceptions=True)

    # 清除用户范围内的 DB 行与磁盘资产（被遗忘权）。
    avatar_rows = (await db.execute(select(AvatarAsset).where(AvatarAsset.user_id == user_id))).scalars().all()
    for av in avatar_rows:
        delete_portrait_file(av.asset_url)

    await db.execute(delete(AvatarAsset).where(AvatarAsset.user_id == user_id))
    await db.execute(delete(Companion3DModel).where(Companion3DModel.user_id == user_id))
    await db.delete(await db.get(User, user_id))
    await db.commit()

    for sub in ("companion-assets", "companion-models"):
        d = Path(SETTINGS.data_dir) / sub / str(user_id)
        if d.exists():
            with contextlib.suppress(Exception):
                if d.is_dir():
                    shutil.rmtree(d, ignore_errors=True)
                else:
                    d.unlink(missing_ok=True)
    return {"message": "用户已删除。"}


@router.patch("/users/{user_id}/toggle-active")
async def toggle_user_active(user_id: int, _admin: str = Depends(get_current_admin_token), db: AsyncSession = Depends(get_db)) -> UserResponse:
    user = await get_or_404(db, User, id=user_id, detail="用户不存在。")
    user.is_active = not user.is_active
    await db.commit()
    return UserResponse.model_validate(user)


def _config_list_item(r: UserModelConfig) -> UserModelConfigListItem:
    return UserModelConfigListItem(
        user_id=r.user_id,
        llm_provider=r.llm_provider or "",
        llm_base_url=r.llm_base_url,
        llm_api_key_set=bool(r.llm_api_key),
        llm_model_name=r.llm_model_name,
        stt_provider=r.stt_provider or "",
        stt_base_url=r.stt_base_url,
        stt_api_key_set=bool(r.stt_api_key),
        stt_model_name=r.stt_model_name,
        tts_provider=r.tts_provider or "",
        tts_base_url=r.tts_base_url,
        tts_api_key_set=bool(r.tts_api_key),
        tts_model_name=r.tts_model_name,
        image_gen_provider=r.image_gen_provider or "",
        image_gen_base_url=r.image_gen_base_url,
        image_gen_api_key_set=bool(r.image_gen_api_key),
        image_gen_model_name=r.image_gen_model_name,
        video_gen_provider=r.video_gen_provider or "",
        video_gen_base_url=r.video_gen_base_url,
        video_gen_api_key_set=bool(r.video_gen_api_key),
        video_gen_model_name=r.video_gen_model_name,
        provider_config=public_provider_slots(r.provider_config),
    )


@router.get("/model-configs", response_model=UserModelConfigListResponse)
async def list_model_configs(_admin: str = Depends(get_current_admin_token), db: AsyncSession = Depends(get_db)) -> UserModelConfigListResponse:
    return UserModelConfigListResponse(items=[_config_list_item(r) for r in (await db.execute(select(UserModelConfig))).scalars().all()])


@router.get("/runtime-info")
async def runtime_info(_admin: str = Depends(get_current_admin_token)) -> dict:
    """把 ``public_base_url`` 暴露给 admin 页：创建账号时自动填进激活码的 ``baseUrl``，留空时前端再降级到 ``http://localhost:10620``。"""
    return {"public_base_url": SETTINGS.public_base_url or ""}


@router.put("/{user_id}/model-config")
async def upsert_model_config(user_id: int, payload: UserModelConfigRequest, _admin: str = Depends(get_current_admin_token), db: AsyncSession = Depends(get_db)) -> MessageResponse:
    # 管理员写入必须三字段齐全；行内字段不全会静默打断用户聊天链路（PROTOCOL §5.4）。
    if not (payload.llm_base_url and payload.llm_api_key and payload.llm_model_name):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="base_url、api_key、model_name 三字段必填。")
    await get_or_404(db, User, id=user_id, detail="用户不存在。")
    config = (await db.execute(select(UserModelConfig).where(UserModelConfig.user_id == user_id))).scalar_one_or_none()
    provider_json = merge_provider_json(payload.provider_config, config)
    if config:
        apply_partial(config, payload, exclude=frozenset({"provider_config"}))
        config.provider_config = provider_json
    else:
        data = payload.model_dump()
        data["provider_config"] = provider_json
        db.add(UserModelConfig(user_id=user_id, **data))
    await db.commit()
    return {"message": "模型配置已更新。"}


@router.delete("/{user_id}/model-config")
async def delete_model_config(user_id: int, _admin: str = Depends(get_current_admin_token), db: AsyncSession = Depends(get_db)) -> MessageResponse:
    await db.delete(await get_or_404(db, UserModelConfig, user_id=user_id, detail="模型配置不存在。"))
    await db.commit()
    return {"message": "模型配置已删除。"}


# ── 用户数据导出 / 导入 ────────────────────────────────────────
# conversations / messages / channel_bindings / login_records / admin_sessions 故意排除；
# activation_code / activation_token_hash 也不复制（绑定原部署 base_url）。


@router.get("/users/{user_id}/export")
async def export_user_backup(
    user_id: int,
    _admin: str = Depends(get_current_admin_token),
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    user = await get_or_404(db, User, id=user_id, detail="用户不存在。")
    rows_by_table = {tbl: await serialize_rows(db, tbl, user_id) for tbl in TABLES}
    files = await collect_files_for_export(user_id, db)

    fd, tmp = tempfile.mkstemp(prefix="spiritagent-export-", suffix=".zip")
    os.close(fd)
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", json.dumps(build_manifest(user, rows_by_table, _admin), ensure_ascii=False))
            for tbl, rs in rows_by_table.items():
                zf.writestr(f"db/{tbl}.json", json.dumps({"table": tbl, "rows": rs}, ensure_ascii=False))
            for src in files:
                arc = "files/" + src.relative_to(Path(SETTINGS.data_dir)).as_posix()
                zf.write(src, arcname=arc, compresslevel=6)
        filename = f"spiritagent-user-{user_id}-{datetime.utcnow():%Y%m%d%H%M%S}.zip"
        return FileResponse(
            path=tmp,
            media_type="application/zip",
            filename=filename,
            background=BackgroundTask(lambda p=tmp: Path(p).unlink(missing_ok=True)),
        )
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


@router.post("/users/{user_id}/import")
async def import_user_backup(
    user_id: int,
    file: UploadFile = File(...),
    mode: str = "overwrite",
    _admin: str = Depends(get_current_admin_token),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """从 zip 恢复用户的角色资产到目标用户。mode=overwrite 先清空；mode=merge 跳过唯一 per-user 表。"""
    if mode not in ("overwrite", "merge"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="mode 必须是 overwrite 或 merge。")
    if not file.filename or not file.filename.endswith(".zip"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="上传文件必须是 .zip。")
    await get_or_404(db, User, id=user_id, detail="用户不存在。")

    with tempfile.TemporaryDirectory(prefix="spiritagent-import-") as tmp_dir:
        zip_path = Path(tmp_dir) / "upload.zip"
        extract_root = Path(tmp_dir) / "extract"
        extract_root.mkdir(parents=True, exist_ok=True)

        # 分块 spool：边读边写边累加，超 500 MB 直接拒
        total = 0
        with open(zip_path, "wb") as out:
            while chunk := await file.read(ARCHIVE_UPLOAD_CHUNK_BYTES):
                total += len(chunk)
                if total > ARCHIVE_MAX_BYTES:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"Archive exceeds {ARCHIVE_MAX_BYTES} bytes",
                    )
                out.write(chunk)

        try:
            zf = zipfile.ZipFile(zip_path, "r")
        except zipfile.BadZipFile:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="无效 zip 文件。") from None

        try:
            extract_resolved = extract_root.resolve()
            for name in zf.namelist():
                target_path = (extract_root / name).resolve()
                if not target_path.is_relative_to(extract_resolved):
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"非法归档条目：{name}")
                target_path.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(name) as src, open(target_path, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        finally:
            zf.close()

        manifest = load_manifest(extract_root)
        source_uid = int(manifest["source_user_id"])
        rows = deserialize_rows(extract_root)

        # 阶段 2：DB + 磁盘恢复必须在 with 块内完成 ——
        # extract_root 随 TemporaryDirectory 退出而清空，restore_files 必须赶在退出前读它。
        if mode == "overwrite":
            await clear_user_scoped_rows(db, user_id)
            for sub in ("companion-assets", "companion-models"):
                d = Path(SETTINGS.data_dir) / sub / str(user_id)
                if d.exists():
                    with contextlib.suppress(Exception):
                        shutil.rmtree(d, ignore_errors=True)

        rewriter = restore_files(extract_root, source_uid, user_id, mode=mode)

        try:
            id_map: dict[str, dict[int, int]] = {}
            for tbl in INSERT_ORDER:
                new_map = await insert_rows(db, tbl, rows.get(tbl, []), user_id, rewriter, id_map, mode=mode)
                id_map[tbl] = new_map
            await db.commit()
        except Exception:
            await db.rollback()
            raise

    return {
        "mode": mode,
        "imported": {tbl: len(rows.get(tbl, [])) for tbl in TABLES},
    }
