#!/usr/bin/env bash
# Build the DeskAgent client installer for macOS.
#
# Single entry point that orchestrates:
#   1. uv build wheel → runner/dist/deskagent-agent-*.whl
#   2. electron-builder → client/release/DeskAgent-{ver}-mac-*.dmg
#   3. Stage payload (runner wheel + desktop + skills + config) to installer/payload/
#   4. Patch tauri.conf.json so bundle.resources contains the current host's
#      desktop artifact (Tauri 2 fails on missing resources).
#   5. Tauri build → installer/src-tauri/target/release/bundle/.../DeskAgent-Setup.dmg
#   6. Restore tauri.conf.json (git state preserved).
#   7. Print the final installer path under release/.
#
# Backend (Docker) is NOT built here — it has its own CI/repo path.
#
# Usage:
#   scripts/build_client.sh --version 0.16.0 --target mac
#
# Options:
#   --version X.Y.Z           Required. Written into desktop + installer package.json
#                             and runner/pyproject.toml.
#   --target mac              Build target. Defaults to current host. macOS must
#                             be built on its native host (electron-builder /
#                             Tauri can't cross-build).
#   --skip-runner             Don't build runner wheel (use existing dist/deskagent-agent-*.whl).
#   --skip-desktop            Don't build desktop (use existing release/DeskAgent-*).
#   --sign-identity ID        macOS code-sign identity (Developer ID Application: ...).
#   --notary-profile NAME     macOS notarytool keychain profile.
#   --output DIR              Output directory for the final installer. Default: release/.

set -euo pipefail

# --- defaults ---------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

VERSION=""
TARGET=""             # resolved later from uname
SKIP_RUNNER=0
SKIP_DESKTOP=0
SIGN_IDENTITY=""
NOTARY_PROFILE=""
OUTPUT_DIR="$REPO_ROOT/release"

DESKTOP_PNPM_TARGET="" # populated per --target
DESKTOP_ARTIFACT_GLOB=""
DESKTOP_FORMAT=""
TAURI_BUNDLE_DIR=""

# --- arg parsing ------------------------------------------------------------

usage() {
  cat <<EOF
Usage: $0 --version X.Y.Z [--target mac] [--skip-runner] [--skip-desktop] \\
       [--sign-identity ID] [--notary-profile NAME] [--output DIR]

Backend (Docker) is built separately. This script only builds the client
(runner + desktop) and bundles them into the Tauri installer.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --version)         VERSION="$2"; shift 2 ;;
    --target)
      TARGET="$2"
      case "$TARGET" in
        mac) ;;
        *) echo "error: --target must be 'mac' (got '$TARGET')" >&2; exit 2 ;;
      esac
      shift 2 ;;
    --skip-runner)     SKIP_RUNNER=1; shift ;;
    --skip-desktop)    SKIP_DESKTOP=1; shift ;;
    --sign-identity)   SIGN_IDENTITY="$2"; shift 2 ;;
    --notary-profile)  NOTARY_PROFILE="$2"; shift 2 ;;
    --output)          OUTPUT_DIR="$2"; shift 2 ;;
    -h|--help)         usage; exit 0 ;;
    *)                 echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$VERSION" ]]; then
  echo "error: --version X.Y.Z is required" >&2
  usage
  exit 2
fi

# --- resolve target from host if not given ----------------------------------

HOST_OS="$(uname -s)"
if [[ -z "$TARGET" ]]; then
  case "$HOST_OS" in
    Darwin)  TARGET="mac" ;;
    *)       echo "error: cannot infer target from host OS '$HOST_OS'. Pass --target." >&2; exit 1 ;;
  esac
fi

# Validate host/target match (no cross-build).
if [[ "$TARGET" == "mac" && "$HOST_OS" != "Darwin" ]]; then
  echo "error: --target mac requires a macOS host (got '$HOST_OS')" >&2
  exit 1
fi
DESKTOP_PNPM_TARGET="dist:mac:dmg"
DESKTOP_ARTIFACT_GLOB="DeskAgent-${VERSION}-mac-*.dmg"
DESKTOP_FORMAT="dmg"
TAURI_BUNDLE_DIR="dmg"

# --- preflight --------------------------------------------------------------

# Build deps — missing any of these aborts the build with a clear message.
# rsync/python3/node are also build deps; jq is consumed later by
# patch_tauri_config and gets its own specific error path so the operator
# knows exactly which step needs it.
for cmd in uv pnpm node python3 rsync; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "error: required build dep '$cmd' not found in PATH" >&2
    exit 1
  fi
done

# macOS build deps.
for cmd in hdiutil codesign; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "error: required command '$cmd' not found in PATH" >&2
    exit 1
  fi
done

# jq is used by patch_tauri_config (further down). Check it here so a
# missing jq fails the build with a clear error pointing at the right
# step, instead of letting it surface later as a cryptic jq parse error.
if ! command -v jq >/dev/null 2>&1; then
  echo "error: 'jq' is required to patch tauri.conf.json for the desktop artifact" >&2
  exit 1
fi

# --- helper functions -------------------------------------------------------

set_version() {
  local v="$1"
  echo "==> Writing version $v to package.json/pyproject.toml"
  python3 - "$v" <<'PY'
import json, sys, re, pathlib
v = sys.argv[1]

# client/package.json
p = pathlib.Path("client/package.json")
data = json.loads(p.read_text())
data["version"] = v
p.write_text(json.dumps(data, indent=2) + "\n")

# installer/package.json
p = pathlib.Path("installer/package.json")
data = json.loads(p.read_text())
data["version"] = v
p.write_text(json.dumps(data, indent=2) + "\n")

# installer/src-tauri/tauri.conf.json
p = pathlib.Path("installer/src-tauri/tauri.conf.json")
data = json.loads(p.read_text())
data["version"] = v
p.write_text(json.dumps(data, indent=2) + "\n")

# installer/src-tauri/Cargo.toml (already has version field)
p = pathlib.Path("installer/src-tauri/Cargo.toml")
text = p.read_text()
text = re.sub(r'^version = "[^"]+"', f'version = "{v}"', text, count=1, flags=re.MULTILINE)
p.write_text(text)

# runner/pyproject.toml
p = pathlib.Path("runner/pyproject.toml")
text = p.read_text()
text = re.sub(r'^version = "[^"]+"', f'version = "{v}"', text, count=1, flags=re.MULTILINE)
p.write_text(text)
PY
}

stage_payload() {
  echo "==> Staging payload in installer/payload/"
  rm -rf installer/payload/runner installer/payload/client
  mkdir -p installer/payload/runner installer/payload/client

  local wheel
  wheel=$(ls -1 runner/dist/deskagent-agent-*.whl 2>/dev/null | head -1 || true)
  if [[ -z "$wheel" ]]; then
    echo "error: no wheel found in runner/dist/ (build runner first)" >&2
    exit 1
  fi
  cp "$wheel" "installer/payload/runner/$(basename "$wheel")"
  # Also copy server.py into the payload so install scripts deploy it to $DESKAGENT_HOME/runner/
  cp runner/server.py installer/payload/runner/server.py

  # Symlink skills/install scripts so they're not duplicated in the repo.
  # build_client.{sh,ps1} run as a single command — symlinks are fine for
  # staging.
  rm -rf installer/payload/skills \
         installer/payload/install.sh \
         installer/payload/install.ps1
  ln -s ../skills installer/payload/skills
  ln -s ../install.sh installer/payload/install.sh
  ln -s ../install.ps1 installer/payload/install.ps1
  echo "    runner: $(ls -l installer/payload/runner/*.whl | awk '{print $5}') bytes"
  echo "    desktop: $(ls -1 installer/payload/client/ | tr '\n' ' ')"
  echo "    install scripts: $(ls -1 installer/payload/install.{sh,ps1} 2>/dev/null | tr '\n' ' ')"
}

write_staging_metadata() {
  local built_at sha_tool
  built_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  sha_tool="sha256sum"
  command -v sha256sum >/dev/null 2>&1 || sha_tool="shasum -a 256"

  echo "==> Writing installer/payload/.staging.json"
  python3 - "$VERSION" "$built_at" "$HOST_OS" "$DESKTOP_FORMAT" <<'PY'
import json, sys, hashlib, os, glob, pathlib
version, built_at, host_os, fmt = sys.argv[1:5]
def sha(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()

meta = {
    "version": version,
    "built_at": built_at,
    "host": f"{host_os}-{os.uname().machine}" if hasattr(os, 'uname') else host_os,
    "desktop_format": fmt,
    "runner_wheel": os.path.basename(sorted(glob.glob("installer/payload/runner/deskagent-agent-*.whl"))[0]),
    "runner_sha256": sha(sorted(glob.glob("installer/payload/runner/deskagent-agent-*.whl"))[0]),
}
desktop_glob = "installer/payload/client/*"
desktops = sorted(glob.glob(desktop_glob))
if desktops:
    meta["desktop_sha256"] = sha(desktops[0])
    meta["desktop_path"] = os.path.basename(desktops[0])
pathlib.Path("installer/payload/.staging.json").write_text(json.dumps(meta, indent=2) + "\n")
print(json.dumps(meta, indent=2))
PY
}

patch_tauri_config() {
  # Replace the desktop .gitkeep placeholder in bundle.resources with the
  # current host's actual desktop artifact path. Tauri 2 fails on missing
  # resources, so we MUST list only what actually exists for this build.
  if ! command -v jq >/dev/null 2>&1; then
    echo "error: 'jq' is required to patch tauri.conf.json" >&2
    exit 1
  fi

  local conf="installer/src-tauri/tauri.conf.json"
  local bak="$conf.build_client.bak"
  cp "$conf" "$bak"

  local desktop_rel="payload/client/$(ls -1 installer/payload/client/ | head -1)"
  echo "==> Patching $conf: bundle.resources → ../${desktop_rel}"
  # Replace the .gitkeep placeholder entry with the actual desktop artifact
  # path. Install scripts and other entries are untouched.
  jq --arg d "../$desktop_rel" \
     '.bundle.resources |= map(if . == "../payload/client/.gitkeep" then $d else . end)' \
     "$conf" > "$conf.new"
  mv "$conf.new" "$conf"
}

restore_tauri_config() {
  local conf="installer/src-tauri/tauri.conf.json"
  local bak="$conf.build_client.bak"
  if [[ -f "$bak" ]]; then
    echo "==> Restoring $conf"
    mv "$bak" "$conf"
  fi
}

# Run a command, but always restore tauri.conf.json on exit (success or fail).
trap restore_tauri_config EXIT

# --- main -------------------------------------------------------------------

cd "$REPO_ROOT"

set_version "$VERSION"

# 1. Build runner.
if [[ $SKIP_RUNNER -eq 0 ]]; then
  echo "==> Building runner (uv build wheel → dist/deskagent-agent-*.whl)"
  # Pre-package gate: catch the env-rot failure mode that previously
  # shipped zero-byte `typing_extensions.py` / `mcp` `.py` files inside
  # the wheel — the install-time smoke was too shallow (`import tools,
  # utils`) and let bad wheels through. See
  # runner/tests/test_startup_imports.py docstring for context.
  ( cd runner && \
      uv sync --frozen --extra dev && \
      uv run --frozen --no-sync pytest tests/ -q && \
      uv build --wheel --out-dir dist ) \
    || { echo "FAIL: runner test suite failed — see pytest output. Common cause: stale or corrupt transitive dep (typing_extensions, mcp, annotated_types) that would make the shipped wheel unstartable on user machines. Fix the env (try \`uv cache clean\` + \`uv sync\`) before retrying the build." >&2; exit 1; }
else
  echo "==> Skipping runner build (--skip-runner)"
fi

# 2. Build client.
if [[ $SKIP_DESKTOP -eq 0 ]]; then
  echo "==> Building client (electron-builder → release/DeskAgent-${VERSION}-${TARGET}*)"
  ( cd client && pnpm install --frozen-lockfile && pnpm run $DESKTOP_PNPM_TARGET )
else
  echo "==> Skipping client build (--skip-desktop)"
fi

# 3. Locate desktop artifact.
DESKTOP_ARTIFACT="$(ls -1 client/release/${DESKTOP_ARTIFACT_GLOB} 2>/dev/null | head -1 || true)"
if [[ -z "$DESKTOP_ARTIFACT" ]]; then
  echo "error: no desktop artifact matching '$DESKTOP_ARTIFACT_GLOB' found in client/release/" >&2
  exit 1
fi
echo "==> Desktop artifact: $DESKTOP_ARTIFACT"

# 4. Stage payload.
stage_payload
cp "$DESKTOP_ARTIFACT" "installer/payload/client/"

# 5. Staging metadata.
write_staging_metadata

# 6. macOS code-sign + notarize.
if [[ -n "$SIGN_IDENTITY" ]]; then
  echo "==> Code-signing $DESKTOP_ARTIFACT"
  cp "$DESKTOP_ARTIFACT" "${DESKTOP_ARTIFACT}.unsigned"
  codesign --deep --force --options runtime --sign "$SIGN_IDENTITY" "$DESKTOP_ARTIFACT"
  if [[ -n "$NOTARY_PROFILE" ]]; then
    echo "==> Notarizing $DESKTOP_ARTIFACT"
    xcrun notarytool submit "$DESKTOP_ARTIFACT" --keychain-profile "$NOTARY_PROFILE" --wait
    xcrun stapler staple "$DESKTOP_ARTIFACT"
  fi
  # Re-copy the signed artifact into the payload.
  cp "$DESKTOP_ARTIFACT" "installer/payload/client/"
fi

# 7. Patch tauri.conf.json for the current host's desktop artifact, then
#    Tauri build, then restore.
patch_tauri_config

echo "==> Tauri build"
( cd installer && pnpm install --frozen-lockfile && pnpm run tauri:build )

# 8. Locate final installer.
FINAL_DIR="installer/src-tauri/target/release/bundle/$TAURI_BUNDLE_DIR"
FINAL_GLOB="DeskAgent-Setup_${VERSION}_*.dmg"
FINAL="$(ls -1 $FINAL_DIR/$FINAL_GLOB 2>/dev/null | head -1 || true)"
if [[ -z "$FINAL" ]]; then
  echo "error: Tauri build did not produce $FINAL_DIR/$FINAL_GLOB" >&2
  exit 1
fi

# 9. Copy to output dir.
mkdir -p "$OUTPUT_DIR"
FINAL_NAME="$(basename "$FINAL")"
cp "$FINAL" "$OUTPUT_DIR/$FINAL_NAME"
echo ""
echo "==> Final installer: $OUTPUT_DIR/$FINAL_NAME"
