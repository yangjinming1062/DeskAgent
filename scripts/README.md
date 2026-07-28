# scripts/

Two classes of artifacts, deliberately kept thin. **Install scripts are NOT here** — they live next to the installer in [installer/install.{sh,ps1,cmd](../installer/README.md) so the Tauri app and its worker scripts ship as one self-contained unit.

## 1. Build client installer — `build_client.{sh,ps1}`

Single entry point that orchestrates **runner (uv build wheel) → desktop (electron-builder) → stage → Tauri (installer)** end-to-end, and at the very end emits `release/DeskAgent-{ver}-update.zip` (`Build-UpdateZip`) which is the self-update artifact the desktop's `electron-updater` channel consumes on next launch.

```bash
scripts/build_client.sh --version 0.16.0 --target mac
scripts/build_client.sh --version 0.16.0 --target linux
pwsh scripts/build_client.ps1 -Version 0.16.0
```

- 写版本号到 `desktop/package.json`、`installer/package.json`、`installer/src-tauri/tauri.conf.json`、`installer/src-tauri/Cargo.toml`、`runner/pyproject.toml`
- Staging 到 `installer/payload/`（symlink skills/config，copy runner + desktop artifact）
- macOS code-sign + notarize（`--sign-identity` / `----notary-profile`）；Windows signtool（`-CertThumbprint`）
- Tauri 2 默认对 `bundle.resources` 缺失文件**报错**；所以 `build_client.{sh,ps1}` 在 tauri build 之前**patch** `tauri.conf.json#bundle.resources` 列表（用 `jq` 或 PowerShell `ConvertFrom-Json`），把 `../payload/desktop/.gitkeep` 占位替换为当前 host 的实际 desktop artifact；build 完后 restore（git 状态保持干净）
- 跨平台 build 不可行 — macOS code-sign 必须 mac host，Windows 必须 win host。脚本会校验 `host/target` 匹配

**Windows 最终产物是单个 `DeskAgent-Setup-{ver}.exe`**（不是 NSIS wrapper）。理由：NSIS wrapper 会出现"双安装器"问题——NSIS 把 `DeskAgent-Setup.exe` 装到 Program Files，用户还得再手动跑一次才看到安装 UI。直接发 `DeskAgent-Setup.exe` 用户双击即看到 Tauri 安装 UI，单文件模式。`build_client.ps1` 用 `tauri build --no-bundle` 跳过 NSIS，产物直接拷贝到 `release/DeskAgent-Setup-{ver}.exe`。

**Backend (Docker) 不参与** — 它有独立的 docker-compose 部署流。

## 2. Import 检查 — `check_imports.py`

backend + runner 的 static import-shape 检查器（被 `.pre-commit-config.yaml` 注册为本地 hook 并以 `--strict-imports` 启动），专门防 c66ab1a 一类回归。当前覆盖 4 类违规：

- `TYPE_CHECKING` 名字泄漏：仅在 `if TYPE_CHECKING:` 内 import、却被类体注解等运行时求值路径引用的名字
- `tools → core` eager import：破坏 `core ↔ tools` 循环的模块级 `from core import ...`
- `runner/tools/<subpkg>` 之间的 sibling cross-subpackage eager import（terminal_tool ↔ file_tools、code_execution_tool → thread_context 这类循环）
- Facade 一致性：`from <local_pkg> import X` 走的 `<local_pkg>` 必须在其 `__init__.py` 里 re-export `X`，防止 facade 被过度精简

---

## Files

| File | Role | Status |
|------|------|--------|
| `build_client.sh` | macOS + Linux client build | Single entry point |
| `build_client.ps1` | Windows client build | Single entry point |
| `check_imports.py` | backend+runner import-shape static check (pre-commit local hook) | 4 checks: TC leak / core↔tools / cross-subpkg / facade |
| `lib/UpdateManifest.ps1` | `Build-UpdateZip` PowerShell 实现 | build_client.ps1 末尾调它产出 `release/DeskAgent-{ver}-update.zip`(签 `latest*.yml` 与 `latest-runner.yml`) |


See [installer/README.md](../installer/README.md) for the install protocol backend (`install.{sh,ps1,cmd}`) that lives there.

## 重构行动项

Scripts 在新定位下**构建链不变**——版本号写入、staging、code-sign、update zip 产出流程全与产品定位无关。配合 DeskAgent 重命名，已落地的改动：

- **保留（不动）**：`build_client.{sh,ps1}` 全链路编排、`check_imports.py` import-shape 检查、`lib/UpdateManifest.ps1` 自更新包签名。
- **完成（DeskAgent 重命名）**：`build_client.{sh,ps1}` 中所有 `Zast-{ver}-{platform}.{ext}` 产物 glob、Tauri cargo bin `Zast-Setup` → `DeskAgent-Setup`、update-zip 文件名 `Zast-{ver}-update.zip` → `DeskAgent-{ver}-update.zip`、runner wheel glob `zast_agent-*.whl` → `deskagent-agent-*.whl`、install-script symlink 名、$ZAST_HOME 注释引用。
- **无新增**：未来若 Desktop 引入新的原生依赖（如精灵渲染库），其打包由 `desktop/package.json` 的 electron-builder 配置承接，scripts 端无需调整。
