# scripts/

两类制品，刻意保持薄。**Install 脚本不在此处** —— 它们与 installer 同目录（[installer/install.{sh,ps1,cmd}](../installer/README.md)），让 Tauri 程序与其 worker 脚本作为一个自洽单元分发。

## 1. 构建安装器 — `build_client.{sh,ps1}`

单一入口，端到端编排 **runner (uv build wheel) → client (electron-builder) → stage → Tauri (installer)**。末尾 `build_client.ps1`（Windows）额外构建 `release/SpiritAgent-{ver}-update.zip`（`Build-UpdateZip`）——合并 client 二进制与 runner wheel 的自更新 artifact，供 client `electron-updater` 消费。`build_client.sh`（macOS）不构建此 zip，各平台 update artifact 由 electron-builder 直接产出。

```bash
scripts/build_client.sh --version 0.16.0 --target mac
pwsh scripts/build_client.ps1 -Version 0.16.0
```

- 写版本号到 `client/package.json`、`installer/package.json`、`installer/src-tauri/tauri.conf.json`、`installer/src-tauri/Cargo.toml`、`runner/pyproject.toml`
- Staging 到 `installer/payload/`（symlink skills 与 install 脚本，copy config + runner wheel + client artifact）
- macOS code-sign + notarize（`--sign-identity` / `--notary-profile`）；Windows signtool（`-CertThumbprint`）
- Tauri 2 默认对 `bundle.resources` 缺失文件**报错**；`build_client.{sh,ps1}` 在 tauri build 之前 patch `tauri.conf.json#bundle.resources` 列表，把 `../payload/client/.gitkeep` 占位替换为当前 host 的实际 client artifact，build 后 restore（git 状态保持干净）
- **跨平台 build 不可行** —— macOS code-sign 必须 mac host，Windows 必须 win host。脚本校验 `host/target` 匹配

**Windows 最终产物是单个 `SpiritAgent-Setup-{ver}.exe`**（不是 NSIS wrapper）。NSIS wrapper 会出现"双安装器"问题——NSIS 把 `SpiritAgent-Setup.exe` 装到 Program Files，用户还得再手动跑一次。直接发 `SpiritAgent-Setup.exe` 用户双击即看到 Tauri 安装 UI。`build_client.ps1` 用 `tauri build --no-bundle` 跳过 NSIS，产物直接拷贝到 `release/SpiritAgent-Setup-{ver}.exe`。

**Backend (Docker) 不参与** —— 有独立的 docker-compose 部署流。

## 2. Import 检查 — `check_imports.py`

backend + runner 的 static import-shape 检查器（被 `.pre-commit-config.yaml` 注册为本地 hook 并以 `--strict-imports` 启动），防 c66ab1a 一类回归。覆盖 3 类违规：

- `TYPE_CHECKING` 名字泄漏：仅在 `if TYPE_CHECKING:` 内 import、却被类体注解等运行时求值路径引用的名字
- `runner/tools/<subpkg>` 之间的 sibling cross-subpackage eager import（terminal_tool ↔ file_tools、code_execution_tool → thread_context 这类循环）
- Facade 一致性：`from <local_pkg> import X` 走的 `<local_pkg>` 必须在其 `__init__.py` 里 re-export `X`，防止 facade 被过度精简

## 3. Onboarding 引导词音频生成与校验 — `onboarding-audio/`

包含预渲染引导词音频元信息 `manifest.json` 与合成/校验脚本 `generate_onboarding_audio.py`。详见 [scripts/onboarding-audio/README.md](onboarding-audio/README.md)。
