import contextlib
import json
import re
import shutil
import zipfile
from pathlib import Path

from common import get_or_404
from common import get_router
from common import list_response
from components import apply_partial
from components import get_db
from components import normalize_sha512
from components import sha512_b64
from fastapi import Depends
from fastapi import File
from fastapi import Form
from fastapi import HTTPException
from fastapi import UploadFile
from fastapi.responses import FileResponse
from modules.auth import get_current_admin_token
from modules.update import UpdateVersion
from modules.update import UpdateVersionItem
from modules.update import UpdateVersionListResponse
from modules.update import UpdateVersionUpdate
from sqlalchemy.orm import Session

router = get_router()

VERSIONS_DIR = Path("updates/versions")
CHUNK_SIZE = 8192

# File suffixes electron-builder emits across the two platforms we ship.
# Squirrel.Windows is the legacy `-Setup.exe`/`-full.nupkg` set; Squirrel.Mac
# uses a `*.zip`. `.dmg` is also kept for the macOS direct-download (not
# consumed by electron-updater, but the admin zip can carry it).
#
# The runner-half suffixes (`server.py`, `.whl`, `latest-runner.yml`,
# `manifest.json`, `app-update.yml`) come from `scripts/build_client.ps1`'s
# `Build-UpdateZip` step. They live in the same admin-upload zip alongside
# the desktop artifacts; the upload handler extracts both halves and
# populates the per-platform DB columns + the four new runner_* columns.
_ALLOWED_ARCHIVE_SUFFIXES = (
    ".exe",
    "RELEASES",
    "-full.nupkg",
    ".blockmap",
    ".zip",
    ".dmg",
    ".whl",
    "server.py",
    "manifest.json",
    "latest-runner.yml",
    "app-update.yml",
)
_DOWNLOAD_SUFFIXES = (
    ".exe",
    "-full.nupkg",
    "-full.nupkg.blockmap",
    ".blockmap",
    ".zip",
    ".dmg",
    ".dmg.blockmap",
    ".whl",
    "server.py",
    "latest-runner.yml",
)


def _get_latest(db: Session) -> UpdateVersion:
    latest = db.query(UpdateVersion).filter(UpdateVersion.is_active.is_(True)).order_by(UpdateVersion.created_at.desc()).first()
    if latest is None:
        raise HTTPException(status_code=404, detail="No active version")
    return latest


def _pick_asset(versions_dir: Path, *patterns: str) -> Path | None:
    """Return the last sorted file in `versions_dir` matching any pattern.

    Patterns are concatenated in call order then sorted, matching the prior
    per-block `sorted(.../*.zip) + sorted(.../*.dmg)` semantics: files matching
    later patterns sort AFTER earlier patterns only when their names tie.
    """
    if not patterns:
        return None
    matches: list[Path] = []
    for pattern in patterns:
        matches.extend(versions_dir.glob(pattern))
    return sorted(matches)[-1] if matches else None


def _build_manifest(latest: UpdateVersion, filename: str | None, sha512: str | None, size: int | None) -> dict:
    """Shape one Squirrel/electron-updater manifest for a single platform asset.

    The filename and hash are the per-platform DB columns; size falls back to a
    live stat when the column is NULL (older rows from before the macOS column
    existed).
    """
    if not filename or not sha512:
        raise HTTPException(status_code=404, detail="No active release for this platform")
    file_path = VERSIONS_DIR / latest.version / filename
    actual_size = size if size is not None else 0
    if actual_size == 0:
        try:
            actual_size = file_path.stat().st_size
        except OSError:
            actual_size = 0
    return {
        "version": latest.version,
        "releaseDate": latest.created_at.isoformat(),
        "releaseNotes": latest.release_notes,
        "path": filename,
        "sha512": normalize_sha512(sha512),
        "files": [{"url": filename, "sha512": normalize_sha512(sha512), "size": actual_size}],
    }


@router.get("/latest.yml")
def get_latest_yml(db: Session = Depends(get_db)) -> dict:
    latest = _get_latest(db)
    return _build_manifest(latest, latest.exe_filename, latest.exe_sha512, latest.exe_size)


@router.get("/latest-mac.yml")
def get_latest_mac_yml(db: Session = Depends(get_db)) -> dict:
    latest = _get_latest(db)
    return _build_manifest(latest, latest.mac_filename, latest.mac_sha512, latest.mac_size)


@router.get("/latest-runner.yml", response_class=FileResponse)
def get_latest_runner_yml(db: Session = Depends(get_db)) -> FileResponse:
    """Serve the signed runner manifest written by `scripts/build_client.ps1`'s
    `Build-UpdateZip` step. The desktop main process reads this BEFORE the
    restart, downloads the wheel + server.py locally, and only AFTER both
    have staged + verified does it allow the user to click "Restart". On next
    launch the new Electron runs `installPending`, which `pip install
    --upgrade` the wheel in place and overwrites `server.py`.
    """
    latest = _get_latest(db)
    if not latest.runner_filename:
        raise HTTPException(status_code=404, detail="No active release with a runner asset")
    manifest_path = VERSIONS_DIR / latest.version / "latest-runner.yml"
    if not manifest_path.exists():
        raise HTTPException(status_code=404, detail="latest-runner.yml not found")
    return FileResponse(path=str(manifest_path), media_type="application/yaml", filename="latest-runner.yml")


@router.get("/versions", response_model=UpdateVersionListResponse)
def list_versions(_admin: str = Depends(get_current_admin_token), db: Session = Depends(get_db)) -> UpdateVersionListResponse:
    records = db.query(UpdateVersion).order_by(UpdateVersion.created_at.desc()).all()
    return list_response(records, UpdateVersionItem, UpdateVersionListResponse)


def _extract_archive_entries(zip_path: Path, versions_dir: Path) -> None:
    """Extract allowed desktop + runner entries from an update zip."""
    versions_dir_resolved = versions_dir.resolve()
    with zipfile.ZipFile(zip_path, "r") as zf:
        for name in zf.namelist():
            if not name.endswith(_ALLOWED_ARCHIVE_SUFFIXES):
                continue
            rel_path = name
            if not rel_path:
                continue
            target = (versions_dir / rel_path).resolve()
            if not target.is_relative_to(versions_dir_resolved):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(name) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)


@router.post("/versions", response_model=UpdateVersionItem, status_code=201)
async def create_version(
    file: UploadFile = File(...),
    release_notes: str = Form(""),
    _admin: str = Depends(get_current_admin_token),
    db: Session = Depends(get_db),
) -> UpdateVersionItem:
    # Squirrel build-output zip; must contain a *.exe.
    if not file.filename or not file.filename.endswith(".zip"):
        raise HTTPException(status_code=400, detail="File must be a .zip file")

    # Constrain to a bare semver — the value becomes a path segment under
    # VERSIONS_DIR, so anything else would let the filename escape the dir.
    if not (match := re.search(r"\d+\.\d+\.\d+", file.filename)):
        raise HTTPException(status_code=400, detail="Filename must contain a version like 1.2.3")
    version = match.group(0)

    if db.query(UpdateVersion).filter(UpdateVersion.version == version).one_or_none():
        raise HTTPException(status_code=400, detail=f"Version {version} already exists")

    versions_dir = VERSIONS_DIR / version
    versions_dir.mkdir(parents=True, exist_ok=True)
    zip_path = versions_dir / ".tmp.zip"
    with open(zip_path, "wb") as f:
        while chunk := await file.read(CHUNK_SIZE):
            f.write(chunk)

    try:
        _extract_archive_entries(zip_path, versions_dir)
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Invalid zip file")
    finally:
        with contextlib.suppress(OSError):
            zip_path.unlink(missing_ok=True)

    # Validate the embedded manifest.json. The build script (Build-UpdateZip)
    # always writes one and its `version` field must match the version we
    # extracted from the upload filename — otherwise the zip is from a
    # different release and we refuse to accept it.
    manifest_path = versions_dir / "manifest.json"
    if not manifest_path.exists():
        shutil.rmtree(versions_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail="Zip must contain manifest.json")
    try:
        manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        shutil.rmtree(versions_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail=f"Invalid manifest.json: {exc}")
    manifest_version = manifest_data.get("version")
    if not manifest_version:
        shutil.rmtree(versions_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail="manifest.json missing required 'version' field")
    if manifest_version != version:
        shutil.rmtree(versions_dir, ignore_errors=True)
        raise HTTPException(
            status_code=400,
            detail=f"manifest.json version {manifest_version} does not match upload filename version {version}",
        )

    exe_file = _pick_asset(versions_dir, "*.exe")
    if not exe_file:
        shutil.rmtree(versions_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail="Zip must contain a *.exe file")

    mac_file = _pick_asset(versions_dir, "*.zip", "*.dmg")

    # Runner-half. The wheel + server.py are extracted into runner/ by
    # _extract_archive_entries; latest-runner.yml lives at the root.
    wheel_file = _pick_asset(versions_dir, "runner/desk_agent-*.whl")

    record = UpdateVersion(
        version=version,
        release_notes=release_notes,
        exe_filename=exe_file.name,
        exe_sha512=sha512_b64(exe_file),
        exe_size=exe_file.stat().st_size,
        mac_filename=mac_file.name if mac_file else None,
        mac_sha512=sha512_b64(mac_file) if mac_file else None,
        mac_size=mac_file.stat().st_size if mac_file else None,
        runner_filename=f"runner/{wheel_file.name}" if wheel_file else None,
        runner_sha512=sha512_b64(wheel_file) if wheel_file else None,
        runner_size=wheel_file.stat().st_size if wheel_file else None,
        runner_version=version if wheel_file else None,
        is_active=True,
        created_by=_admin,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return UpdateVersionItem.model_validate(record)


@router.patch("/versions/{id}", response_model=UpdateVersionItem)
def update_version(id: int, payload: UpdateVersionUpdate, _admin: str = Depends(get_current_admin_token), db: Session = Depends(get_db)) -> UpdateVersionItem:
    record = get_or_404(db, UpdateVersion, id=id, detail="Version not found")
    apply_partial(record, payload)
    db.commit()
    return UpdateVersionItem.model_validate(record)


@router.delete("/versions/{id}")
def delete_version(id: int, _admin: str = Depends(get_current_admin_token), db: Session = Depends(get_db)) -> dict:
    record = get_or_404(db, UpdateVersion, id=id, detail="Version not found")
    versions_dir = VERSIONS_DIR / record.version
    if versions_dir.exists():
        shutil.rmtree(versions_dir)
    db.delete(record)
    db.commit()
    return {"message": "Version deleted"}


@router.get("/{filename:path}", response_class=FileResponse)
def get_latest_file(filename: str, db: Session = Depends(get_db)) -> FileResponse:
    latest = _get_latest(db)
    if not any(filename.endswith(s) for s in _DOWNLOAD_SUFFIXES):
        raise HTTPException(status_code=400, detail="Invalid filename")
    base_dir = (VERSIONS_DIR / latest.version).resolve()
    file_path = (base_dir / filename).resolve()
    if not file_path.is_relative_to(base_dir):
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path=str(file_path), media_type="application/octet-stream", filename=filename)
