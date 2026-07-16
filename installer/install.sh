#!/usr/bin/env bash
# Zast Agent installer (POSIX / macOS / Linux).
#
# 6-stage payload release. Tauri Zast-Setup.app is the GUI shell that
# spawns this script; the script's job is to install Python (if needed),
# copy the bundled runner binary, desktop app, skills, and config.yaml
# into the user's $ZAST_HOME (and platform-canonical locations for the
# desktop app).
#
# Protocol:
#   install.sh -Manifest                 → emit manifest JSON, one stage list
#   install.sh -Stage NAME -Json         → run a single stage, emit result frame
#
# Payload locations are passed via ZAST_BUNDLE_* env vars (set by the Tauri
# installer) or via the matching --bundled-*-dir CLI args (for dev/test).
# When both are present, env wins.

set -euo pipefail
shopt -s nullglob

PROTOCOL_VERSION=2
SCRIPT_NAME="install.sh"
PYTHON_VERSION="3.13"

# --- defaults ---------------------------------------------------------------

# Default ZAST_HOME. Overridden by $ZAST_HOME or --zast-home.
DEFAULT_ZAST_HOME_UNIX="$HOME/.zast"

# Path to the runner binary inside the bundle. POSIX uses no extension.
RUNNER_WHEEL_GLOB="zast_agent-*.whl"

# Default desktop format; overridden by $ZAST_INSTALLER_FORMAT.
DEFAULT_DESKTOP_FORMAT="dmg"

# --- arg parsing ------------------------------------------------------------

ZAST_HOME_ARG=""
BUNDLED_RUNNER_DIR_ARG=""
BUNDLED_DESKTOP_DIR_ARG=""
BUNDLED_SKILLS_DIR_ARG=""
CONFIG_PATH_ARG=""

MODE="stage"     # "manifest" | "stage"
STAGE=""
JSON_OUTPUT=0
NON_INTERACTIVE=0

usage() {
  cat <<EOF
$SCRIPT_NAME — Zast Agent installer (6-stage payload release)

Usage:
  $SCRIPT_NAME -Manifest
  $SCRIPT_NAME -Stage NAME [-Json] [-NonInteractive] \\
      [--zast-home PATH] \\
      [--bundled-runner-dir PATH] \\
      [--bundled-desktop-dir PATH] \\
      [--bundled-skills-dir PATH] \\
      [--config-path PATH]

Stages: welcome, install-python, unpack-runner, unpack-desktop, install-skills, write-config.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -Manifest|--manifest)         MODE="manifest"; shift ;;
    -Stage|--stage)               MODE="stage"; STAGE="$2"; shift 2 ;;
    -Json|--json)                 JSON_OUTPUT=1; shift ;;
    -NonInteractive|--non-interactive) NON_INTERACTIVE=1; shift ;;
    --zast-home)                  ZAST_HOME_ARG="$2"; shift 2 ;;
    --bundled-runner-dir)         BUNDLED_RUNNER_DIR_ARG="$2"; shift 2 ;;
    --bundled-desktop-dir)        BUNDLED_DESKTOP_DIR_ARG="$2"; shift 2 ;;
    --bundled-skills-dir)         BUNDLED_SKILLS_DIR_ARG="$2"; shift 2 ;;
    --config-path)                CONFIG_PATH_ARG="$2"; shift 2 ;;
    -h|--help)                    usage; exit 0 ;;
    *)                            echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

# --- resolve paths: env var > arg > default ---------------------------------

if [[ -n "${ZAST_HOME:-}" ]]; then
  ZAST_HOME_RESOLVED="$ZAST_HOME"
elif [[ -n "$ZAST_HOME_ARG" ]]; then
  ZAST_HOME_RESOLVED="$ZAST_HOME_ARG"
else
  ZAST_HOME_RESOLVED="$DEFAULT_ZAST_HOME_UNIX"
fi

BUNDLED_RUNNER_DIR="${ZAST_BUNDLED_RUNNER_DIR:-$BUNDLED_RUNNER_DIR_ARG}"
BUNDLED_DESKTOP_DIR="${ZAST_BUNDLED_DESKTOP_DIR:-$BUNDLED_DESKTOP_DIR_ARG}"
BUNDLED_SKILLS_DIR="${ZAST_BUNDLED_SKILLS_DIR:-$BUNDLED_SKILLS_DIR_ARG}"
CONFIG_PATH="${ZAST_CONFIG_PATH:-$CONFIG_PATH_ARG}"
DESKTOP_FORMAT="${ZAST_INSTALLER_FORMAT:-$DEFAULT_DESKTOP_FORMAT}"

# --- output helpers ---------------------------------------------------------

emit_manifest() {
  cat <<EOF
{"protocol_version": ${PROTOCOL_VERSION}, "stages": [
  {"name": "welcome", "title": "准备安装", "category": "setup", "needs_user_input": false},
  {"name": "install-python", "title": "安装 Python 运行时", "category": "prereqs", "needs_user_input": false},
  {"name": "unpack-runner", "title": "安装 Zast 运行器", "category": "payload", "needs_user_input": false},
  {"name": "unpack-desktop", "title": "安装 Zast 桌面应用", "category": "payload", "needs_user_input": false},
  {"name": "install-skills", "title": "安装内置技能", "category": "payload", "needs_user_input": false},
  {"name": "write-config", "title": "写入配置文件", "category": "finalize", "needs_user_input": false}
]}
EOF
}

# emit_stage_ok <stage> [skipped=0|1] [reason]
emit_stage_ok() {
  local stage="$1" skipped="${2:-0}" reason="${3:-}"
  if [[ "$skipped" == "1" && -n "$reason" ]]; then
    # Escape double quotes in reason so the JSON frame stays valid.
    local esc="${reason//\\/\\\\}"
    esc="${esc//\"/\\\"}"
    printf '{"ok": true, "stage": "%s", "skipped": true, "reason": "%s"}\n' "$stage" "$esc"
  else
    printf '{"ok": true, "stage": "%s"}\n' "$stage"
  fi
}

# emit_stage_err <stage> <reason>
emit_stage_err() {
  local stage="$1" reason="$2"
  local esc="${reason//\\/\\\\}"
  esc="${esc//\"/\\\"}"
  printf '{"ok": false, "stage": "%s", "reason": "%s"}\n' "$stage" "$esc"
}

# --- Python installation helpers --------------------------------------------

install_uv() {
  local managed_uv="$ZAST_HOME_RESOLVED/bin/uv"
  if [[ -f "$managed_uv" ]]; then
    UV_CMD="$managed_uv"
    return 0
  fi

  mkdir -p "$ZAST_HOME_RESOLVED/bin"

  if command -v curl >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | UV_INSTALL_DIR="$ZAST_HOME_RESOLVED/bin" sh 2>/dev/null
  elif command -v wget >/dev/null 2>&1; then
    wget -qO- https://astral.sh/uv/install.sh | UV_INSTALL_DIR="$ZAST_HOME_RESOLVED/bin" sh 2>/dev/null
  else
    return 1
  fi

  if [[ -f "$managed_uv" ]]; then
    UV_CMD="$managed_uv"
    return 0
  fi
  return 1
}

test_python() {
  if [[ -z "${UV_CMD:-}" ]]; then
    if ! install_uv; then
      return 1
    fi
  fi

  local candidates=("$PYTHON_VERSION" "3.12" "3.14" "3.11")
  local missing=()

  for ver in "${candidates[@]}"; do
    local found
    found=$("$UV_CMD" python find "$ver" 2>/dev/null || true)
    if [[ -n "$found" ]]; then
      PYTHON_VERSION="$ver"
      return 0
    fi
    missing+=("$ver")
  done

  # Cold cache — install the preferred version and re-check just that one.
  "$UV_CMD" python install "$PYTHON_VERSION" 2>/dev/null || true
  local found
  found=$("$UV_CMD" python find "$PYTHON_VERSION" 2>/dev/null || true)
  if [[ -n "$found" ]]; then
    return 0
  fi

  return 1
}

# --- stage 1: welcome -------------------------------------------------------

stage_welcome() {
  mkdir -p "$ZAST_HOME_RESOLVED/bin" \
           "$ZAST_HOME_RESOLVED/skills" \
           "$ZAST_HOME_RESOLVED/logs"

  if [[ ! -d "$ZAST_HOME_RESOLVED" ]]; then
    emit_stage_err welcome "could not create ZAST_HOME: $ZAST_HOME_RESOLVED"
    return 1
  fi
  if [[ ! -w "$ZAST_HOME_RESOLVED" ]]; then
    emit_stage_err welcome "ZAST_HOME not writable: $ZAST_HOME_RESOLVED"
    return 1
  fi

  local marker="$ZAST_HOME_RESOLVED/.zast-bootstrap-complete"
  local is_reinstall="false"
  [[ -f "$marker" ]] && is_reinstall="true"

  printf '{"ok": true, "stage": "welcome", "data": {"zast_home": "%s", "is_reinstall": %s}}\n' \
    "$ZAST_HOME_RESOLVED" "$is_reinstall"
}

# --- stage 2: install-python ------------------------------------------------

stage_install_python() {
  if test_python; then
    printf '{"ok": true, "stage": "install-python", "data": {"version": "%s"}}\n' "$PYTHON_VERSION"
    return 0
  fi

  emit_stage_err install-python "Python $PYTHON_VERSION is required but could not be installed. Install Python manually from https://www.python.org/downloads/ and re-run."
  return 1
}

# --- stage 3: unpack-runner -------------------------------------------------

stage_unpack_runner() {
  if [[ -z "$BUNDLED_RUNNER_DIR" ]]; then
    emit_stage_err unpack-runner "--bundled-runner-dir (or ZAST_BUNDLED_RUNNER_DIR) is required"
    return 1
  fi
  if [[ ! -d "$BUNDLED_RUNNER_DIR" ]]; then
    emit_stage_err unpack-runner "bundled runner dir not found: $BUNDLED_RUNNER_DIR"
    return 1
  fi

  local wheel
  wheel=("$BUNDLED_RUNNER_DIR"/$RUNNER_WHEEL_GLOB)
  if [[ ${#wheel[@]} -eq 0 || ! -f "${wheel[0]}" ]]; then
    emit_stage_err unpack-runner "wheel not found in $BUNDLED_RUNNER_DIR"
    return 1
  fi
  wheel="${wheel[0]}"

  local runner_dir="$ZAST_HOME_RESOLVED/runner"
  mkdir -p "$runner_dir"

  # Copy server.py alongside the wheel
  if [[ -f "$BUNDLED_RUNNER_DIR/server.py" ]]; then
    cp -f "$BUNDLED_RUNNER_DIR/server.py" "$runner_dir/server.py"
  fi

  # `--clear` is load-bearing on a reinstall: without it `uv venv` errors out
  # when the target dir already exists, leaving a stale/broken venv in place —
  # exactly the env-rot failure mode this stage is designed to recover from.
  # install.ps1 uses the same flag; keep the two scripts in sync.
  "$UV_CMD" venv "$runner_dir/.venv" --python "$PYTHON_VERSION" --clear 2>/dev/null || {
    emit_stage_err unpack-runner "uv venv failed"
    return 1
  }

  # Install wheel into venv
  "$UV_CMD" pip install --python "$runner_dir/.venv/bin/python" "$wheel" 2>/dev/null || {
    emit_stage_err unpack-runner "uv pip install failed"
    return 1
  }

  # No post-install smoke: build_client.{ps1,sh} already gates the
  # wheel behind pytest tests/test_startup_imports.py before packaging,
  # so a broken venv can't reach users through this installer.
  # install.sh stays simple.

  # Clean up old PyInstaller binary if present
  rm -f "$ZAST_HOME_RESOLVED/bin/zast-runner"

  local size
  size=$(stat -c%s "$wheel" 2>/dev/null || stat -f%z "$wheel" 2>/dev/null || echo 0)
  printf '{"ok": true, "stage": "unpack-runner", "data": {"venv": "%s/runner/.venv", "wheel": "%s", "size_bytes": %s}}\n' \
    "$ZAST_HOME_RESOLVED" "$(basename "$wheel")" "$size"
}

# --- stage 4: unpack-desktop ------------------------------------------------

stage_unpack_desktop() {
  if [[ -z "$BUNDLED_DESKTOP_DIR" ]]; then
    emit_stage_err unpack-desktop "--bundled-desktop-dir (or ZAST_BUNDLED_DESKTOP_DIR) is required"
    return 1
  fi
  if [[ ! -d "$BUNDLED_DESKTOP_DIR" ]]; then
    emit_stage_err unpack-desktop "bundled desktop dir not found: $BUNDLED_DESKTOP_DIR"
    return 1
  fi

  # Locate the artifact by format.
  local artifact=""
  case "$DESKTOP_FORMAT" in
    dmg)
      artifact=$(ls -1 "$BUNDLED_DESKTOP_DIR"/*.dmg 2>/dev/null | head -1 || true)
      ;;
    AppImage)
      artifact=$(ls -1 "$BUNDLED_DESKTOP_DIR"/*.AppImage 2>/dev/null | head -1 || true)
      ;;
    nsis)
      artifact=$(ls -1 "$BUNDLED_DESKTOP_DIR"/*.exe 2>/dev/null | head -1 || true)
      ;;
    zip)
      artifact=$(ls -1 "$BUNDLED_DESKTOP_DIR"/*.zip 2>/dev/null | head -1 || true)
      ;;
    *)
      emit_stage_err unpack-desktop "unknown desktop format: $DESKTOP_FORMAT"
      return 1
      ;;
  esac

  if [[ -z "$artifact" || ! -f "$artifact" ]]; then
    emit_stage_err unpack-desktop "no desktop artifact found in $BUNDLED_DESKTOP_DIR (format=$DESKTOP_FORMAT)"
    return 1
  fi

  case "$DESKTOP_FORMAT" in
    dmg)
      # macOS: mount DMG, copy Zast.app to /Applications, detach, strip xattrs.
      if [[ "$(uname -s)" != "Darwin" ]]; then
        emit_stage_err unpack-desktop "dmg format requires macOS host"
        return 1
      fi
      local mount_point
      mount_point=$(hdiutil attach -nobrowse -readonly "$artifact" 2>/dev/null | awk '/\/Volumes/{print $3; exit}')
      if [[ -z "$mount_point" ]]; then
        emit_stage_err unpack-desktop "failed to mount $artifact"
        return 1
      fi
      if [[ ! -d "$mount_point/Zast.app" ]]; then
        hdiutil detach "$mount_point" 2>/dev/null || true
        emit_stage_err unpack-desktop "Zast.app not found in DMG $artifact"
        return 1
      fi
      rm -rf /Applications/Zast.app
      cp -R "$mount_point/Zast.app" /Applications/Zast.app
      hdiutil detach "$mount_point" 2>/dev/null || true
      xattr -cr /Applications/Zast.app 2>/dev/null || true
      printf '{"ok": true, "stage": "unpack-desktop", "data": {"installed_path": "/Applications/Zast.app", "format": "dmg"}}\n'
      ;;
    AppImage)
      # Linux: copy AppImage to $ZAST_HOME/bin/, chmod, write .desktop entry.
      local appimage_name
      appimage_name=$(basename "$artifact")
      mkdir -p "$ZAST_HOME_RESOLVED/bin"
      cp -f "$artifact" "$ZAST_HOME_RESOLVED/bin/$appimage_name"
      chmod +x "$ZAST_HOME_RESOLVED/bin/$appimage_name"
      mkdir -p "$HOME/.local/share/applications"
      cat > "$HOME/.local/share/applications/zast.desktop" <<EOF2
[Desktop Entry]
Type=Application
Name=Zast
Exec=$ZAST_HOME_RESOLVED/bin/$appimage_name %U
Icon=zast
Terminal=false
Categories=Development;
EOF2
      printf '{"ok": true, "stage": "unpack-desktop", "data": {"installed_path": "%s/bin/%s", "format": "AppImage"}}\n' \
        "$ZAST_HOME_RESOLVED" "$appimage_name"
      ;;
    nsis|zip)
      # install.sh doesn't run on Windows; on POSIX this format is unsupported
      # (Windows users get install.ps1 via the Tauri installer instead).
      emit_stage_err unpack-desktop "desktop format '$DESKTOP_FORMAT' is not supported on this platform (use install.ps1 on Windows)"
      return 1
      ;;
  esac
}

# --- stage 5: install-skills ------------------------------------------------

stage_install_skills() {
  if [[ -z "$BUNDLED_SKILLS_DIR" ]]; then
    emit_stage_err install-skills "--bundled-skills-dir (or ZAST_BUNDLED_SKILLS_DIR) is required"
    return 1
  fi
  if [[ ! -d "$BUNDLED_SKILLS_DIR" ]]; then
    emit_stage_err install-skills "bundled skills dir not found: $BUNDLED_SKILLS_DIR"
    return 1
  fi

  # Respect the .no-bundled-skills marker (set by --no-skills / zast profile).
  if [[ -f "$ZAST_HOME_RESOLVED/.no-bundled-skills" ]]; then
    emit_stage_ok install-skills 1 "user opted out via .no-bundled-skills"
    return 0
  fi

  # rsync without --delete to preserve any skills the user added locally.
  mkdir -p "$ZAST_HOME_RESOLVED/skills"
  if command -v rsync >/dev/null 2>&1; then
    rsync -a "$BUNDLED_SKILLS_DIR/" "$ZAST_HOME_RESOLVED/skills/"
  else
    cp -R "$BUNDLED_SKILLS_DIR/." "$ZAST_HOME_RESOLVED/skills/"
  fi

  local bundled_count
  bundled_count=$(find "$ZAST_HOME_RESOLVED/skills" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l | tr -d ' ')

  printf '{"ok": true, "stage": "install-skills", "data": {"bundled_count": %s}}\n' \
    "$bundled_count"
}

# --- stage 6: write-config --------------------------------------------------

stage_write_config() {
  if [[ -z "$CONFIG_PATH" ]]; then
    emit_stage_err write-config "--config-path (or ZAST_CONFIG_PATH) is required"
    return 1
  fi
  if [[ ! -f "$CONFIG_PATH" ]]; then
    emit_stage_err write-config "config not found: $CONFIG_PATH"
    return 1
  fi

  local dst="$ZAST_HOME_RESOLVED/config.yaml"
  cp -f "$CONFIG_PATH" "$dst"

  # bootstrap-complete marker — installer/CLAUDE.md §6 fast path checks this.
  : > "$ZAST_HOME_RESOLVED/.zast-bootstrap-complete"

  printf '{"ok": true, "stage": "write-config", "data": {"config": "%s", "marker": "%s/.zast-bootstrap-complete"}}\n' \
    "$dst" "$ZAST_HOME_RESOLVED"
}

# --- dispatch ---------------------------------------------------------------

case "$MODE" in
  manifest)
    emit_manifest
    ;;
  stage)
    if [[ -z "$STAGE" ]]; then
      echo "error: --stage NAME is required (or pass -Manifest)" >&2
      exit 2
    fi
    case "$STAGE" in
      welcome)         stage_welcome ;;
      install-python)  stage_install_python ;;
      unpack-runner)   stage_unpack_runner ;;
      unpack-desktop)  stage_unpack_desktop ;;
      install-skills)  stage_install_skills ;;
      write-config)    stage_write_config ;;
      *)
        emit_stage_err "$STAGE" "unknown stage"
        exit 1
        ;;
    esac
    ;;
esac
