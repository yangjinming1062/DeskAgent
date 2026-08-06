#!/usr/bin/env bash
# DeskAgent Agent installer (POSIX / macOS).
#
# 6-stage payload release. Tauri DeskAgent-Setup.app is the GUI shell that
# spawns this script; the script's job is to install Python (if needed),
# copy the bundled runner binary, desktop app, skills, and config.yaml
# into the user's $DESKAGENT_HOME (and platform-canonical locations for the
# desktop app).
#
# Protocol:
#   install.sh -Manifest                 → emit manifest JSON, one stage list
#   install.sh -Stage NAME -Json         → run a single stage, emit result frame
#
# Payload locations are passed via DESKAGENT_BUNDLE_* env vars (set by the Tauri
# installer) or via the matching --bundled-*-dir CLI args (for dev/test).
# When both are present, env wins.

set -euo pipefail
shopt -s nullglob

PROTOCOL_VERSION=2
SCRIPT_NAME="install.sh"
PYTHON_VERSION="3.13"

# --- defaults ---------------------------------------------------------------

# Default DESKAGENT_HOME. Overridden by $DESKAGENT_HOME or --deskagent-home.
DEFAULT_DESKAGENT_HOME_UNIX="$HOME/.deskagent"

# Path to the runner binary inside the bundle. POSIX uses no extension.
RUNNER_WHEEL_GLOB="desk_agent-*.whl"

# Default desktop format; overridden by $DESKAGENT_INSTALLER_FORMAT.
DEFAULT_DESKTOP_FORMAT="dmg"

# --- arg parsing ------------------------------------------------------------

DESKAGENT_HOME_ARG=""
BUNDLED_RUNNER_DIR_ARG=""
BUNDLED_DESKTOP_DIR_ARG=""
BUNDLED_SKILLS_DIR_ARG=""
BUNDLED_VOICES_DIR_ARG=""
BUNDLED_ONBOARDING_AUDIO_DIR_ARG=""
CONFIG_PATH_ARG=""

MODE="stage"     # "manifest" | "stage"
STAGE=""
JSON_OUTPUT=0
NON_INTERACTIVE=0

usage() {
  cat <<EOF
$SCRIPT_NAME — DeskAgent Agent installer (6-stage payload release)

Usage:
  $SCRIPT_NAME -Manifest
  $SCRIPT_NAME -Stage NAME [-Json] [-NonInteractive] \\
      [--deskagent-home PATH] \\
      [--bundled-runner-dir PATH] \\
      [--bundled-desktop-dir PATH] \\
      [--bundled-skills-dir PATH] \\
      [--bundled-voices-dir PATH] \
      [--bundled-onboarding-audio-dir PATH] \\
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
    --deskagent-home)                  DESKAGENT_HOME_ARG="$2"; shift 2 ;;
    --bundled-runner-dir)         BUNDLED_RUNNER_DIR_ARG="$2"; shift 2 ;;
    --bundled-desktop-dir)        BUNDLED_DESKTOP_DIR_ARG="$2"; shift 2 ;;
    --bundled-skills-dir)         BUNDLED_SKILLS_DIR_ARG="$2"; shift 2 ;;
    --bundled-voices-dir)         BUNDLED_VOICES_DIR_ARG="$2"; shift 2 ;;
    --bundled-onboarding-audio-dir) BUNDLED_ONBOARDING_AUDIO_DIR_ARG="$2"; shift 2 ;;
    --config-path)                CONFIG_PATH_ARG="$2"; shift 2 ;;
    -h|--help)                    usage; exit 0 ;;
    *)                            echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

# --- resolve paths: env var > arg > default ---------------------------------

if [[ -n "${DESKAGENT_HOME:-}" ]]; then
  DESKAGENT_HOME_RESOLVED="$DESKAGENT_HOME"
elif [[ -n "$DESKAGENT_HOME_ARG" ]]; then
  DESKAGENT_HOME_RESOLVED="$DESKAGENT_HOME_ARG"
else
  DESKAGENT_HOME_RESOLVED="$DEFAULT_DESKAGENT_HOME_UNIX"
fi

BUNDLED_RUNNER_DIR="${DESKAGENT_BUNDLED_RUNNER_DIR:-$BUNDLED_RUNNER_DIR_ARG}"
BUNDLED_DESKTOP_DIR="${DESKAGENT_BUNDLED_DESKTOP_DIR:-$BUNDLED_DESKTOP_DIR_ARG}"
BUNDLED_SKILLS_DIR="${DESKAGENT_BUNDLED_SKILLS_DIR:-$BUNDLED_SKILLS_DIR_ARG}"
BUNDLED_VOICES_DIR="${DESKAGENT_BUNDLED_VOICES_DIR:-$BUNDLED_VOICES_DIR_ARG}"
BUNDLED_ONBOARDING_AUDIO_DIR="${DESKAGENT_BUNDLED_ONBOARDING_AUDIO_DIR:-$BUNDLED_ONBOARDING_AUDIO_DIR_ARG}"
CONFIG_PATH="${DESKAGENT_CONFIG_PATH:-$CONFIG_PATH_ARG}"
DESKTOP_FORMAT="${DESKAGENT_INSTALLER_FORMAT:-$DEFAULT_DESKTOP_FORMAT}"

# --- output helpers ---------------------------------------------------------

emit_manifest() {
  cat <<EOF
{"protocol_version": ${PROTOCOL_VERSION}, "stages": [
  {"name": "welcome", "title": "准备安装", "category": "setup", "needs_user_input": false},
  {"name": "install-python", "title": "安装 Python 运行时", "category": "prereqs", "needs_user_input": false},
  {"name": "unpack-runner", "title": "安装 DeskAgent 运行器", "category": "payload", "needs_user_input": false},
  {"name": "unpack-desktop", "title": "安装 DeskAgent 桌面应用", "category": "payload", "needs_user_input": false},
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
  local managed_uv="$DESKAGENT_HOME_RESOLVED/bin/uv"
  if [[ -f "$managed_uv" ]]; then
    UV_CMD="$managed_uv"
    return 0
  fi

  mkdir -p "$DESKAGENT_HOME_RESOLVED/bin"

  if command -v curl >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | UV_INSTALL_DIR="$DESKAGENT_HOME_RESOLVED/bin" sh 2>/dev/null
  elif command -v wget >/dev/null 2>&1; then
    wget -qO- https://astral.sh/uv/install.sh | UV_INSTALL_DIR="$DESKAGENT_HOME_RESOLVED/bin" sh 2>/dev/null
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
  mkdir -p "$DESKAGENT_HOME_RESOLVED/bin" \
           "$DESKAGENT_HOME_RESOLVED/skills" \
           "$DESKAGENT_HOME_RESOLVED/logs"

  if [[ ! -d "$DESKAGENT_HOME_RESOLVED" ]]; then
    emit_stage_err welcome "could not create DESKAGENT_HOME: $DESKAGENT_HOME_RESOLVED"
    return 1
  fi
  if [[ ! -w "$DESKAGENT_HOME_RESOLVED" ]]; then
    emit_stage_err welcome "DESKAGENT_HOME not writable: $DESKAGENT_HOME_RESOLVED"
    return 1
  fi

  local marker="$DESKAGENT_HOME_RESOLVED/.deskagent-bootstrap-complete"
  local is_reinstall="false"
  [[ -f "$marker" ]] && is_reinstall="true"

  printf '{"ok": true, "stage": "welcome", "data": {"deskagent_home": "%s", "is_reinstall": %s}}\n' \
    "$DESKAGENT_HOME_RESOLVED" "$is_reinstall"
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
    emit_stage_err unpack-runner "--bundled-runner-dir (or DESKAGENT_BUNDLED_RUNNER_DIR) is required"
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

  local runner_dir="$DESKAGENT_HOME_RESOLVED/runner"
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

  # Install wheel into venv. PyPI direct connection is unreliable from
  # China; on the first failure, retry against the Aliyun mirror which
  # install.ps1 also uses (P2-10: keep the two scripts symmetric). The
  # retry is best-effort: a second failure surfaces the same error to
  # the stage handler.
  if ! "$UV_CMD" pip install --python "$runner_dir/.venv/bin/python" "$wheel" 2>/dev/null; then
    if ! "$UV_CMD" pip install --python "$runner_dir/.venv/bin/python" \
        --index-url https://mirrors.aliyun.com/pypi/simple/ \
        "$wheel" 2>/dev/null; then
      emit_stage_err unpack-runner "uv pip install failed (PyPI + Aliyun mirror)"
      return 1
    fi
  fi

  # No post-install smoke: build_client.{ps1,sh} already gates the
  # wheel behind pytest tests/test_startup_imports.py before packaging,
  # so a broken venv can't reach users through this installer.
  # install.sh stays simple.

  # Clean up old PyInstaller binary if present
  rm -f "$DESKAGENT_HOME_RESOLVED/bin/deskagent-runner"

  # Copy bundled Piper voices (installer/payload/voices/) into the models
  # directory so local TTS works offline on day 1. Each voice is an onnx
  # model + a config json — both files are required; partial copies make
  # Piper raise FileNotFoundError. Voice selection in tts_tool honours the
  # config.yaml::audio.tts.default_voice setting; this just makes sure the
  # on-disk side of the contract is satisfied.
  local voice_count=0
  if [[ -n "$BUNDLED_VOICES_DIR" && -d "$BUNDLED_VOICES_DIR" ]]; then
    local voices_target="$DESKAGENT_HOME_RESOLVED/models/piper"
    mkdir -p "$voices_target"
    shopt -s nullglob
    local voice_files=("$BUNDLED_VOICES_DIR"/*)
    if [[ ${#voice_files[@]} -gt 0 ]]; then
      # Content-based copy, not name-based — a single onnx without its
      # .onnx.json sibling is useless to Piper. Copy anything that ships
      # with both halves so future voice additions only need a payload
      # drop, no install-script edit.
      for f in "${voice_files[@]}"; do
        local name
        name=$(basename "$f")
        if [[ "$name" == *.onnx.json ]]; then
          local stem="${name%.onnx.json}"
          if [[ -f "$BUNDLED_VOICES_DIR/${stem}.onnx" ]]; then
            cp -f "$f" "$voices_target/$name"
          fi
        elif [[ "$name" == *.onnx ]]; then
          # ``${name}.onnx.json`` would expand to ``<name>.onnx.onnx.json``
          # (duplicate .onnx) because ``name`` already ends in .onnx.
          # Append the canonical suffix to the file id stem instead.
          if [[ -f "$BUNDLED_VOICES_DIR/${name}.json" ]]; then
            cp -f "$f" "$voices_target/$name"
          fi
        fi
      done
      voice_count=$(find "$voices_target" -maxdepth 1 -name '*.onnx' -type f | wc -l | tr -d ' ')
    fi
    shopt -u nullglob
  fi

  # Copy bundled onboarding guidance audio — language subdirs (zh/, en/, …) map
  # 1:1 to $DESKAGENT_HOME/audio/onboarding/<lang>/.
  local audio_count=0
  if [[ -n "$BUNDLED_ONBOARDING_AUDIO_DIR" && -d "$BUNDLED_ONBOARDING_AUDIO_DIR" ]]; then
    for lang_dir in "$BUNDLED_ONBOARDING_AUDIO_DIR"/*/; do
      [[ -d "$lang_dir" ]] || continue
      local lang
      lang=$(basename "$lang_dir")
      local audio_target="$DESKAGENT_HOME_RESOLVED/audio/onboarding/$lang"
      mkdir -p "$audio_target"
      cp -R "$lang_dir"/. "$audio_target"/
    done
    audio_count=$(find "$DESKAGENT_HOME_RESOLVED/audio/onboarding" -name '*.mp3' -type f | wc -l | tr -d ' ')
  fi

  local size
  size=$(stat -c%s "$wheel" 2>/dev/null || stat -f%z "$wheel" 2>/dev/null || echo 0)
  printf '{"ok": true, "stage": "unpack-runner", "data": {"venv": "%s/runner/.venv", "wheel": "%s", "size_bytes": %s, "voices_copied": %s, "onboarding_audio_copied": %s}}\n' \
    "$DESKAGENT_HOME_RESOLVED" "$(basename "$wheel")" "$size" "$voice_count" "$audio_count"
}

# --- stage 4: unpack-desktop ------------------------------------------------

stage_unpack_desktop() {
  if [[ -z "$BUNDLED_DESKTOP_DIR" ]]; then
    emit_stage_err unpack-desktop "--bundled-desktop-dir (or DESKAGENT_BUNDLED_DESKTOP_DIR) is required"
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
      # macOS: mount DMG, copy DeskAgent.app to /Applications, detach, strip xattrs.
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
      if [[ ! -d "$mount_point/DeskAgent.app" ]]; then
        hdiutil detach "$mount_point" 2>/dev/null || true
        emit_stage_err unpack-desktop "DeskAgent.app not found in DMG $artifact"
        return 1
      fi
      rm -rf /Applications/DeskAgent.app
      cp -R "$mount_point/DeskAgent.app" /Applications/DeskAgent.app
      hdiutil detach "$mount_point" 2>/dev/null || true
      xattr -cr /Applications/DeskAgent.app 2>/dev/null || true
      printf '{"ok": true, "stage": "unpack-desktop", "data": {"installed_path": "/Applications/DeskAgent.app", "format": "dmg"}}\n'
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
    emit_stage_err install-skills "--bundled-skills-dir (or DESKAGENT_BUNDLED_SKILLS_DIR) is required"
    return 1
  fi
  if [[ ! -d "$BUNDLED_SKILLS_DIR" ]]; then
    emit_stage_err install-skills "bundled skills dir not found: $BUNDLED_SKILLS_DIR"
    return 1
  fi

  # Respect the .no-bundled-skills marker (set by --no-skills / deskagent profile).
  if [[ -f "$DESKAGENT_HOME_RESOLVED/.no-bundled-skills" ]]; then
    emit_stage_ok install-skills 1 "user opted out via .no-bundled-skills"
    return 0
  fi

  # rsync without --delete to preserve any skills the user added locally.
  mkdir -p "$DESKAGENT_HOME_RESOLVED/skills"
  if command -v rsync >/dev/null 2>&1; then
    rsync -a "$BUNDLED_SKILLS_DIR/" "$DESKAGENT_HOME_RESOLVED/skills/"
  else
    cp -R "$BUNDLED_SKILLS_DIR/." "$DESKAGENT_HOME_RESOLVED/skills/"
  fi

  local bundled_count
  bundled_count=$(find "$DESKAGENT_HOME_RESOLVED/skills" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l | tr -d ' ')

  printf '{"ok": true, "stage": "install-skills", "data": {"bundled_count": %s}}\n' \
    "$bundled_count"
}

# --- stage 6: write-config --------------------------------------------------

stage_write_config() {
  if [[ -z "$CONFIG_PATH" ]]; then
    emit_stage_err write-config "--config-path (or DESKAGENT_CONFIG_PATH) is required"
    return 1
  fi
  if [[ ! -f "$CONFIG_PATH" ]]; then
    emit_stage_err write-config "config not found: $CONFIG_PATH"
    return 1
  fi

  local dst="$DESKAGENT_HOME_RESOLVED/config.yaml"
  cp -f "$CONFIG_PATH" "$dst"

  # bootstrap-complete marker — installer/CLAUDE.md §6 fast path checks this.
  : > "$DESKAGENT_HOME_RESOLVED/.deskagent-bootstrap-complete"

  printf '{"ok": true, "stage": "write-config", "data": {"config": "%s", "marker": "%s/.deskagent-bootstrap-complete"}}\n' \
    "$dst" "$DESKAGENT_HOME_RESOLVED"
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
