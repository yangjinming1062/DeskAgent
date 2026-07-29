# installer/

DeskAgent 安装器产品。本目录容纳 **Tauri 2 桌面程序**（`src/` + `src-tauri/`，产 `DeskAgent-Setup.exe` / `DeskAgent-Setup.app` / `deskagent-setup`）、它要释放的安装 payload（`skills/`、`config.yaml`）、以及 Tauri 进程 spawn 出来的 **install 协议后端**（`install.sh` / `install.ps1` / `install.cmd`）。首装完成释放 Desktop 后，Desktop 以"蛋"形态首次启动，进入 [design.md §2](../design.md) 的伙伴生命周期 onboarding。

## 1. 顶层模块解耦

**installer 是独立的顶级模块，禁止依赖 `desktop/`、`backend/`、`runner/` 的资源**——任何"借用"对方样式 / 组件 / 构建产物的做法都会让 Stage 2.x 的边界退化。`src/styles.css` 的设计 token、`src/components/button.tsx` 的 button variants、`fit-text` 工具类等**均为本地副本**；桌面端的设计系统演进时，installer 端独立决定是否跟随，不自动 link。

manifest 字段、`install.{ps1,sh}` 的 stage 名、JSON-RPC frame 等**协议级契约**可以跨模块引用——这些是公共接口，不是私有资源。

**Install 脚本为什么在 `installer/` 而不是 `scripts/`**：`install.{sh,ps1,cmd}` 是 Tauri 进程的 worker（被 `src-tauri/src/powershell.rs::run_script` spawn 出来干活），dev fallback 解析在 `DESKAGENT_SETUP_DEV_REPO_ROOT/installer/`，生产路径从 Tauri `bundle.resources` (`payload/install.{sh,ps1}`) 读取——与 installer 是 1:1 耦合关系，放在 `installer/` 让该模块自洽。`scripts/build_client.{sh,ps1}` 是跨 runner/desktop/installer 三模块的 repo 级 orchestrator，留在 `scripts/`。

**Installer binary is self-contained** —— install 脚本由 `scripts/build_client.{sh,ps1}` 在 `stage_payload()` 时硬链接 / 符号链接到 `installer/payload/`，Tauri 自动嵌入。`DeskAgent-Setup` 二进制运行时**零网络依赖**：不下载 install 脚本，不下载 payload（payload 都在 `bundle.resources` 里），不查更新（更新流走 backend `updates/` HTTP endpoint）。

**Desktop 启动后不做 install / uninstall** —— Desktop 启动时只连云端 Backend，不调 `install.ps1` / `DeskAgent-Setup --uninstall`。本模块（Tauri DeskAgent-Setup）拥有所有平台变更职责，desktop 是只读消费方。Electron 二进制自更新由 desktop 自己负责（`electron-updater` 从 Backend `/api/update` 拉取），详见 [desktop/README.md](../desktop/README.md)。

## 2. 同目录三类内容

- **`src/` + `src-tauri/`**：Tauri 2 桌面程序源码（Rust 后端 + React 19 前端），编译产物是签名好的安装器二进制。Tauri 包名 `@deskagent/installer`、cargo bin 名 `DeskAgent-Setup`、lib 名 `deskagent_bootstrap_lib`。
- **`install.sh` / `install.ps1` / `install.cmd`**：6-stage install 协议后端（welcome / install-python / unpack-runner / unpack-desktop / install-skills / write-config）。`install.cmd` 是 Windows 下 `install.ps1` 在 cmd.exe 不可用时的 dev fallback（`bundle.resources` 不嵌入）。完整规范见 §4。
- **`skills/`、`config.yaml`**：安装 payload，被 `install-skills` / `write-config` stage 读出来 seed 到 `$DESKAGENT_HOME/{skills,config.yaml}`（路径契约详 [runner/README.md §Skills 系统](../runner/README.md)）。

**源位置 vs 嵌入位置**：`installer/skills/`（源，仓库随附）由 `scripts/build_client.{sh,ps1}` 的 `stage_payload()` 在 build 期建立 symlink 到 `installer/payload/skills/`，再由 Tauri `bundle.resources` 嵌入到 `DeskAgent-Setup` 二进制。install 脚本运行时读的是 `<bundle>/payload/skills/`（= §3 的 `DESKAGENT_BUNDLED_SKILLS_DIR`），不是源位置。

**Skills frontmatter**：`SKILL.md` 用 frontmatter 声明 `platforms: [macos | windows | linux]`（缺省表示全平台）。安装器对 frontmatter 完全透明——不解析，按文件整体 seed 到 `$DESKAGENT_HOME/skills/`；filter / 翻译由 Desktop `electron/lib/skill-index.cjs` 和 Runner `tools/skills/skills_tool.py::skill_matches_platform` 各自做（两套翻译表语义对齐，新增平台需同步更新两边）。

## 3. Tauri `bundle.resources` 嵌入 payload

`tauri.conf.json#bundle.resources` 包含以下条目（路径相对 `src-tauri/`）：

- `../payload/runner/` — Runner wheel（`deskagent-agent-*.whl`）+ `server.py`
- `../payload/skills` — symlink 到 `installer/skills/`
- `../payload/config.yaml` — Runner 默认配置（运行期释放到 `~/.deskagent/config.yaml`）
- `../payload/.staging.json` — build metadata（version / sha256 / host）
- `../payload/desktop/.gitkeep` — **占位符**。`scripts/build_client` 在 tauri build 前 patch 为实际 desktop artifact，build 后 restore
- `../payload/install.sh` / `../payload/install.ps1` — install 协议后端脚本
- `../payload/install.cmd` — Windows cmd.exe 兜底（dev 路径，生产 Tauri 不嵌入）

Tauri 进程把 bundle.resources 解压根通过 env var 传给 `install.{sh,ps1}`：

| Env var | 含义 |
|---------|------|
| `DESKAGENT_BUNDLE_DIR` | bundle.resources 解压根 |
| `DESKAGENT_BUNDLED_RUNNER_DIR` | `<bundle>/payload/runner/` |
| `DESKAGENT_BUNDLED_DESKTOP_DIR` | `<bundle>/payload/desktop/` |
| `DESKAGENT_BUNDLED_SKILLS_DIR` | `<bundle>/payload/skills/` |
| `DESKAGENT_CONFIG_PATH` | `<bundle>/payload/config.yaml` |
| `DESKAGENT_INSTALLER_FORMAT` | `dmg` / `nsis` / `AppImage` / `msi` / `zip` |

`install.{sh,ps1}` 6-stage protocol 从这些路径读 payload 并释放到 `$DESKAGENT_HOME` 和平台标准位置。env var 优先、CLI arg 兜底（dev/test 用 `--bundled-*-dir`）。

## 4. 引导协议（bootstrap 流程）

`DeskAgent-Setup.exe` 启动后调用 `installer/install.ps1 -Manifest`（macOS/Linux 是 `installer/install.sh`）拿 stage 列表，再逐 stage 用 `-Stage NAME -NonInteractive -Json` 执行。**6 个 stage**：`install-python` 检测/安装 Python 运行时（通过 uv，目标版本 3.13），其余 5 个 stage 专做 payload 释放——`install.{sh,ps1}` 不做构建（runner / desktop 都是预构建产物，由 Tauri `bundle.resources` 嵌入）。

完整规范在 `src-tauri/src/bootstrap.rs`，前端通过 Tauri `bootstrap` event channel 拿阶段进度。当前 install 协议为 v2（含 `install-python` stage）。

## 5. 平台标准安装位置

`bootstrap::desktop_install_root()` 给出 desktop 的平台-canonical 路径：

- **macOS**: `/Applications/DeskAgent.app`（与 macOS 启动器约定一致）
- **Windows**: `%LOCALAPPDATA%\Programs\DeskAgent\`（NSIS `/D=` 目标）
- **Linux**: `$DESKAGENT_HOME/bin/`（AppImage 落地）

`install.sh` `Stage-UnpackDesktop` 的 macOS 分支用 `hdiutil attach` + `cp -R` + `xattr -cr`；Windows 分支用 `Start-Process /S /D=...` 调 NSIS 静默安装；Linux 分支 `cp +x` AppImage + 写 `~/.local/share/applications/deskagent.desktop`。

## 6. macOS fast path

macOS 上若 `deskagent_is_installed() == true`（存在 `$DESKAGENT_HOME/.deskagent-bootstrap-complete` 标记 + `/Applications/DeskAgent.app/Contents/MacOS/DeskAgent` 可启动），Tauri 直接 relaunch 桌面并退出——让 `/Applications/DeskAgent.app` 兼任"首次启动走安装、之后是 launcher"。`--reinstall` / `--repair` 跳过 fast path，强制显示安装 UI。

## 7. Install 脚本解析 —— 自包含，无网络

`install_script.rs::resolve` 的解析顺序：

1. **Dev shortcut**：`DESKAGENT_SETUP_DEV_REPO_ROOT` env var 找到 `<root>/installer/install.{ps1,sh}` 直接用。dev 模式迭代用，无需重打包 Tauri。
2. **Bundled**（生产路径）：`app.path().resource_dir()/payload/install.{ps1,sh}` —— 由 Tauri `bundle.resources` 嵌入到 `DeskAgent-Setup` 二进制内。

DeskAgent-Setup 二进制**不**下载 install 脚本（项目不放在 GitHub，无可下载源）。脚本版本 = installer build 版本。

## 8. Python 运行时安装

Skills 中的 Python 脚本需要系统 Python。`install-python` stage 通过 [uv](https://docs.astral.sh/uv/) 安装：

1. 检查 `$DESKAGENT_HOME/bin/uv` 是否存在（托管 uv）
2. 不存在则从 `https://astral.sh/uv/install.ps1` 下载安装到 `$DESKAGENT_HOME/bin/`
3. 用 `uv python install <version>` 安装 Python 到 uv 托管位置
4. 支持 Python 3.13（首选）+ 3.12/3.14/3.11 回退

Python 通过 uv 安装到 uv 管理位置，无需管理员权限。

## 9. Runner 安装布局

Runner 通过 `uv build --wheel` 构建为 `deskagent-agent-*.whl`。安装器在 `unpack-runner` stage 将 wheel 安装到 uv-managed venv：

```
$DESKAGENT_HOME/runner/
├── server.py              # 从 tauri payload 复制（不随 wheel）
└── .venv/                 # uv venv（安装器创建）
    ├── bin/python          # POSIX
    ├── Scripts/python.exe  # Windows
    └── lib/.../site-packages/
        ├── tools/          # wheel 安装的 top-level package
        └── utils/          # wheel 安装的 top-level package
```

`unpack-runner` stage 流程：找 wheel → 复制 `server.py` → `uv venv` 创建 venv → `uv pip install <wheel>` → 冒烟 `import tools, utils` → 清理旧 `deskagent-runner{,.exe}`。

Desktop spawn 命令：`$DESKAGENT_HOME/runner/.venv/{bin/python,Scripts/python.exe} $DESKAGENT_HOME/runner/server.py --desktop-ws <ws-url>`。运行时路径 knob 仍是 `DESKAGENT_HOME`。

## 已知限制

- **蛋形象不随 installer 分发**：角色定义完成前的"蛋"占位形象由 Desktop 内置默认渲染（`BrandMark` 组件），不经 installer seed payload。这避免了 payload 与形象资产版本耦合——installer 只负责代码与运行时分发，形象资产完全由 Backend 生成并下发（[design.md §7](../design.md)）。
