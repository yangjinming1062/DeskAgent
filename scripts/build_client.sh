#!/usr/bin/env bash
# 打包 SpiritAgent 客户端安装器（macOS）。Backend 由 Docker 单独构建，不在此入口内。
# 编排顺序：构建 runner wheel → electron-builder 桌面端 → 暂存 payload → 临时改 tauri.conf.json → Tauri 构建 → 还原配置 → 拷贝最终安装器。
# 用法：scripts/build_client.sh --version X.Y.Z [--target mac] [--skip-runner] [--skip-desktop] [--sign-identity ID] [--notary-profile NAME] [--output DIR]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

VERSION=""
TARGET=""
SKIP_RUNNER=0
SKIP_DESKTOP=0
SIGN_IDENTITY=""
NOTARY_PROFILE=""
OUTPUT_DIR="$REPO_ROOT/release"

DESKTOP_PNPM_TARGET=""
DESKTOP_ARTIFACT_GLOB=""
DESKTOP_FORMAT=""
TAURI_BUNDLE_DIR=""

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

# 缺省从 uname 推断目标平台
HOST_OS="$(uname -s)"
if [[ -z "$TARGET" ]]; then
  case "$HOST_OS" in
    Darwin)  TARGET="mac" ;;
    *)       echo "error: cannot infer target from host OS '$HOST_OS'. Pass --target." >&2; exit 1 ;;
  esac
fi

# 不允许跨主机构建；macOS 只能在 Darwin 上产出。
if [[ "$TARGET" == "mac" && "$HOST_OS" != "Darwin" ]]; then
  echo "error: --target mac requires a macOS host (got '$HOST_OS')" >&2
  exit 1
fi
DESKTOP_PNPM_TARGET="dist:mac:dmg"
DESKTOP_ARTIFACT_GLOB="SpiritAgent-${VERSION}-mac-*.dmg"
DESKTOP_FORMAT="dmg"
TAURI_BUNDLE_DIR="dmg"

# 通用构建依赖；缺则报错并打印缺哪一个。
for cmd in uv pnpm node python3 rsync; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "error: required build dep '$cmd' not found in PATH" >&2
    exit 1
  fi
done

# macOS 专属依赖
for cmd in hdiutil codesign; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "error: required command '$cmd' not found in PATH" >&2
    exit 1
  fi
done

# jq 在下方 patch_tauri_config 阶段才用到；这里提前校验，便于给出指向明确步骤的错误。
if ! command -v jq >/dev/null 2>&1; then
  echo "error: 'jq' is required to patch tauri.conf.json for the desktop artifact" >&2
  exit 1
fi

set_version() {
  local v="$1"
  echo "==> Writing version $v to package.json/pyproject.toml"
  python3 - "$v" <<'PY'
import json, sys, re, pathlib
v = sys.argv[1]

p = pathlib.Path("client/package.json")
data = json.loads(p.read_text())
data["version"] = v
p.write_text(json.dumps(data, indent=2) + "\n")

p = pathlib.Path("installer/package.json")
data = json.loads(p.read_text())
data["version"] = v
p.write_text(json.dumps(data, indent=2) + "\n")

p = pathlib.Path("installer/src-tauri/tauri.conf.json")
data = json.loads(p.read_text())
data["version"] = v
p.write_text(json.dumps(data, indent=2) + "\n")

# Cargo.toml 已含 version 字段，直接就地替换
p = pathlib.Path("installer/src-tauri/Cargo.toml")
text = p.read_text()
text = re.sub(r'^version = "[^"]+"', f'version = "{v}"', text, count=1, flags=re.MULTILINE)
p.write_text(text)

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
  wheel=$(ls -1 runner/dist/spiritagent-agent-*.whl 2>/dev/null | head -1 || true)
  if [[ -z "$wheel" ]]; then
    echo "error: no wheel found in runner/dist/ (build runner first)" >&2
    exit 1
  fi
  cp "$wheel" "installer/payload/runner/$(basename "$wheel")"
  # server.py 也拷到 payload，便于 install 脚本部署到 $SPIRITAGENT_HOME/runner/。
  cp runner/server.py installer/payload/runner/server.py

  # 用符号链入 skills/install 脚本，避免在仓库内出现重复副本；
  # build_client.{sh,ps1} 是单次调用，staging 阶段用软链足够。
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

patch_tauri_config() {
  if ! command -v jq >/dev/null 2>&1; then
    echo "error: 'jq' is required to patch tauri.conf.json" >&2
    exit 1
  fi

  local conf="installer/src-tauri/tauri.conf.json"
  local bak="$conf.build_client.bak"
  cp "$conf" "$bak"

  local desktop_rel="payload/client/$(ls -1 installer/payload/client/ | head -1)"
  echo "==> Patching $conf: bundle.resources += ../${desktop_rel}"
  jq --arg d "../$desktop_rel" \
     '.bundle.resources += [$d]' \
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

cd "$REPO_ROOT"

set_version "$VERSION"

if [[ $SKIP_RUNNER -eq 0 ]]; then
  echo "==> Building runner (uv build wheel → dist/spiritagent-agent-*.whl)"
  # 打包前的环境闸口：env-rot 状态（如 0 字节 typing_extensions.py / mcp 的 .py）会跟着钻进 wheel，安装期 smoke 仅 `import tools, utils` 太浅，曾放走坏 wheel。详见 runner/tests/test_startup_imports.py docstring。
  ( cd runner && \
      uv sync --frozen --extra dev && \
      uv run --frozen --no-sync pytest tests/ -q && \
      uv build --wheel --out-dir dist ) \
    || { echo "FAIL: runner test suite failed — see pytest output. Common cause: stale or corrupt transitive dep (typing_extensions, mcp, annotated_types) that would make the shipped wheel unstartable on user machines. Fix the env (try \`uv cache clean\` + \`uv sync\`) before retrying the build." >&2; exit 1; }
else
  echo "==> Skipping runner build (--skip-runner)"
fi

if [[ $SKIP_DESKTOP -eq 0 ]]; then
  echo "==> Building client (electron-builder → release/SpiritAgent-${VERSION}-${TARGET}*)"
  ( cd client && pnpm install --frozen-lockfile && pnpm run $DESKTOP_PNPM_TARGET )
else
  echo "==> Skipping client build (--skip-desktop)"
fi

DESKTOP_ARTIFACT="$(ls -1 client/release/${DESKTOP_ARTIFACT_GLOB} 2>/dev/null | head -1 || true)"
if [[ -z "$DESKTOP_ARTIFACT" ]]; then
  echo "error: no desktop artifact matching '$DESKTOP_ARTIFACT_GLOB' found in client/release/" >&2
  exit 1
fi
echo "==> Desktop artifact: $DESKTOP_ARTIFACT"

stage_payload
cp "$DESKTOP_ARTIFACT" "installer/payload/client/"

# macOS 签名 + 公证
if [[ -n "$SIGN_IDENTITY" ]]; then
  echo "==> Code-signing $DESKTOP_ARTIFACT"
  cp "$DESKTOP_ARTIFACT" "${DESKTOP_ARTIFACT}.unsigned"
  codesign --deep --force --options runtime --sign "$SIGN_IDENTITY" "$DESKTOP_ARTIFACT"
  if [[ -n "$NOTARY_PROFILE" ]]; then
    echo "==> Notarizing $DESKTOP_ARTIFACT"
    xcrun notarytool submit "$DESKTOP_ARTIFACT" --keychain-profile "$NOTARY_PROFILE" --wait
    xcrun stapler staple "$DESKTOP_ARTIFACT"
  fi
  # 重新拷回 payload，覆盖未签名版本。
  cp "$DESKTOP_ARTIFACT" "installer/payload/client/"
fi

patch_tauri_config

echo "==> Tauri build"
( cd installer && pnpm install --frozen-lockfile && pnpm run tauri:build )

FINAL_DIR="installer/src-tauri/target/release/bundle/$TAURI_BUNDLE_DIR"
FINAL_GLOB="SpiritAgent-Setup_${VERSION}_*.dmg"
FINAL="$(ls -1 $FINAL_DIR/$FINAL_GLOB 2>/dev/null | head -1 || true)"
if [[ -z "$FINAL" ]]; then
  echo "error: Tauri build did not produce $FINAL_DIR/$FINAL_GLOB" >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR"
FINAL_NAME="$(basename "$FINAL")"
cp "$FINAL" "$OUTPUT_DIR/$FINAL_NAME"
echo ""
echo "==> Final installer: $OUTPUT_DIR/$FINAL_NAME"
