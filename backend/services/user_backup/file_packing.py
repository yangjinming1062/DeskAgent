import shutil
from pathlib import Path

from components import SETTINGS, get_logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)


class UrlRewriter:
    def __init__(self, mapping: dict[str, str]) -> None:
        self._mapping = mapping

    def __call__(self, original_url: str | None) -> str | None:
        if original_url is None or original_url == "":
            return original_url
        return self._mapping.get(original_url, original_url)


async def collect_files_for_export(user_id: int, db: AsyncSession) -> list[Path]:
    from modules.companion import AvatarAsset, CompanionExpressionAvatar

    data_dir = Path(SETTINGS.data_dir)
    out: list[Path] = sorted((data_dir / "companion-assets" / str(user_id)).glob("*"))
    if (data_dir / "companion-models" / str(user_id)).exists():
        out.extend(sorted((data_dir / "companion-models" / str(user_id)).glob("*")))
    flat_dir = data_dir / "companion-avatars"
    avatar_rows = (await db.execute(select(AvatarAsset).where(AvatarAsset.user_id == user_id))).scalars().all()
    expr_rows = (await db.execute(select(CompanionExpressionAvatar).where(CompanionExpressionAvatar.user_id == user_id))).scalars().all()
    referenced: set[str] = set()
    for asset in avatar_rows:
        referenced.update(_basename(getattr(asset, c) or "") for c in ("asset_url", "seed_front_2d_url", "seed_front_3d_url", "seed_back_url"))
    referenced.update(_basename(ea.asset_url or "") for ea in expr_rows)
    out.extend(flat_dir / name for name in sorted(referenced) if (flat_dir / name).exists())
    return [p for p in out if p.is_file()]


def _basename(url_or_path: str) -> str:
    return url_or_path.rsplit("/", 1)[-1] if url_or_path else ""


def restore_files(extract_root: Path, source_uid: int, target_uid: int, *, mode: str) -> UrlRewriter:
    """把解压根目录 files/* 拷到目标用户磁盘空间，返回 URL 重写表。

    per-user 命名空间即使字节相同也写 mapping（DB 列里的 source_uid 路径不存在于 target，
    必须重写到 target_uid）；flat 命名空间字节相同则跳过拷贝并保留原 URL。
    """
    data_dir = Path(SETTINGS.data_dir)
    mapping: dict[str, str] = {}
    files_root = extract_root / "files"
    if not files_root.exists():
        return UrlRewriter(mapping)

    for sub in ("companion-assets", "companion-models"):
        src_dir = files_root / sub / str(source_uid)
        if not src_dir.exists():
            continue
        dst_dir = data_dir / sub / str(target_uid)
        dst_dir.mkdir(parents=True, exist_ok=True)
        for src in sorted(src_dir.iterdir()):
            if not src.is_file():
                continue
            original_url = f"{sub}/{source_uid}/{src.name}"
            target_url = f"{sub}/{target_uid}/{src.name}"
            if (dst_dir / src.name).exists():
                if _files_equal(src, dst_dir / src.name):
                    mapping[original_url] = target_url  # 跳过拷贝但仍要重写
                    continue
                unique = _alloc_unique(dst_dir / src.name)
                shutil.copy2(src, unique)
                mapping[original_url] = f"{sub}/{target_uid}/{unique.name}"
            else:
                shutil.copy2(src, dst_dir / src.name)
                mapping[original_url] = target_url

    flat_src = files_root / "companion-avatars"
    if flat_src.exists():
        flat_dst = data_dir / "companion-avatars"
        flat_dst.mkdir(parents=True, exist_ok=True)
        for src in sorted(flat_src.iterdir()):
            if not src.is_file():
                continue
            original_url = f"companion-avatars/{src.name}"
            dst = flat_dst / src.name
            if dst.exists():
                if _files_equal(src, dst):
                    mapping[original_url] = original_url
                    continue
                dst = _alloc_unique(dst)
            shutil.copy2(src, dst)
            mapping[original_url] = f"companion-avatars/{dst.name}"

    logger.info("backup restore_files done", extra={"source_uid": source_uid, "target_uid": target_uid, "mode": mode, "mapped": len(mapping)})
    return UrlRewriter(mapping)


def _files_equal(a: Path, b: Path) -> bool:
    try:
        if a.stat().st_size != b.stat().st_size:
            return False
    except OSError:
        return False

    return _sha256(a) == _sha256(b)


def _sha256(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(256 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _alloc_unique(dst: Path) -> Path:
    stem, suffix, parent, n = dst.stem, dst.suffix, dst.parent, 1
    while True:
        candidate = parent / f"{stem}_imp{n}{suffix}"
        if not candidate.exists():
            return candidate
        n += 1
