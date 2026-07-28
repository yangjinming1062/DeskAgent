# installer/

Zast 安装器产品。本目录容纳 **Tauri 2 桌面程序**（`src/` + `src-tauri/`，产 `Zast-Setup.exe` / `Zast-Setup.app` / `zast-setup`）、它要释放的安装 payload（`skills/`、`config.yaml`）、以及 Tauri 进程 spawn 出来的 **install 协议后端**（`install.sh` / `install.ps1` / `install.cmd`）。首装完成释放 Desktop 后，Desktop 以"蛋"形态首次启动，进入 [design.md §2](../design.md) 的伙伴生命周期 onboarding。

## 1. 顶层模块解耦

**installer 是独立的顶级模块，禁止依赖 `desktop/`、`backend/`、`runner/` 的资源**——任何"借用"对方样式 / 组件 / 构建产物的做法都会让 Stage 2.x 的边界再退化。`src/styles.css` 的设计 token、`src/components/button.tsx` 的 button variants、`fit-text` 工具类等**均为本地副本**；当桌面端的设计系统演进时，installer 端需独立决定是否跟随，不要自动 link。

manifest 字段、`install.{ps1,sh}` 的 stage 名、JSON-RPC frame 等**协议级契约**可以跨模块引用——这些是公共接口，不是私有资源。

**Install 脚本为什么在 `installer/` 而不是 `scripts/`**：`install.{sh,ps1,cmd}` 是 Tauri 进程的 worker（被 `src-tauri/src/powershell.rs::run_script` spawn 出来干活），dev fallback 解析在 `ZAST_SETUP_DEV_REPO_ROOT/installer/`，生产路径从 Tauri `bundle.resources` (`payload/install.{sh,ps1}`) 读取——它们与 installer 是 1:1 耦合关系，放在 `installer/` 让该模块自洽。`scripts/build_client.{sh,ps1}` 是另一类（跨 runner/desktop/installer 三个模块的 repo 级 orchestrator），仍留在 `scripts/`。

**Installer binary is self-contained** —— install 脚本由 `scripts/build_client.{sh,ps1}` 在 `stage_payload()` 时硬链接 / 符号链接到 `installer/payload/`，Tauri 自动嵌入。`Zast-Setup` 二进制运行时**零网络依赖**：不下载 install 脚本，不下载 payload（payload 都在 `bundle.resources` 里），不查更新（更新流走 `backend/updates/` HTTP endpoint，由 backend 提供）。

**Desktop 启动后不做 install / uninstall** —— [desktop/README.md §"安装 / 卸载 / 更新"](../desktop/README.md) 明确 desktop 启动时只连 cloud Backend，不调 `install.ps1` / `Zast-Setup --uninstall`。本模块（Tauri Zast-Setup）拥有所有平台变更职责，desktop 是只读消费方。

**Electron 二进制自更新由 desktop 自己负责** —— Desktop 通过 `electron-updater` 从 Backend `/api/update` 拉取预构建产物自更新(desktop 二进制 + Python runner wheel + `server.py`,两半一起装)。详见 [desktop/README.md §"Electron 二进制自更新"](../desktop/README.md#electron-二进制自更新)。

## 2. 同目录三类内容

- **`src/` + `src-tauri/`**：Tauri 2 桌面程序源码（Rust 后端 + React 19 前端），编译产物是签名好的安装器二进制。Tauri 包名 `@zast/installer`、cargo bin 名 `Zast-Setup`、lib 名 `zast_bootstrap_lib`。
- **`install.sh` / `install.ps1` / `install.cmd`**：6-stage install 协议后端（welcome / install-python / unpack-runner / unpack-desktop / install-skills / write-config）。Tauri `Zast-Setup` 启动后 spawn 它们干活。`install.cmd` 是 Windows 下 `install.ps1` 在 cmd.exe 环境不可用时的 dev fallback;`bundle.resources` 不嵌入(走 `ZAST_SETUP_DEV_REPO_ROOT/installer/install.cmd` dev 解析路径)。完整规范见 §4。
- **`skills/`、`config.yaml`**：安装 payload，被 `install.{ps1,sh}` 的 `install-skills` / `write-config` stage 读出来 seed 到 `$ZAST_HOME/{skills,config.yaml}`（路径契约详 [runner/README.md §Skills 系统](../runner/README.md#skills-系统)）。

`install.{ps1,sh}` 已经在路径 `<repo>/installer/skills/` 寻找 payload——和 Tauri 源码同居让 payload 路径与安装脚本的预期天然对齐，无需在 install 脚本里维护"另一份位置"。

**源位置 vs 嵌入位置**：`installer/skills/`（源，仓库随附）由 `scripts/build_client.{sh,ps1}` 的 `stage_payload()` 在 build 期建立 symlink 到 `installer/payload/skills/`，再由 Tauri `bundle.resources` 嵌入到 `Zast-Setup` 二进制。install 脚本运行时读的是 `<bundle>/payload/skills/`（= §3 的 `ZAST_BUNDLED_SKILLS_DIR`），不是源位置；其它文档引用 skills 时应使用 payload 路径（用户实际看到的）。

**Skills frontmatter 与平台**：`SKILL.md` 用 frontmatter 声明 `platforms: [macos | windows | linux]`（缺省表示全平台）。`installer/skills/desktop/computer-use/` 跨 macOS + Windows（`name: computer-use`，`platforms: [macos, windows]`），由 cua-driver（macOS）/ UI Automation（Windows）自动挑选 backend；用户无论安装哪个 OS 的 Zast，看到的就是同一个 `computer-use`，内容也是同一份"Computer Use (universal, any-model, macOS + Windows)"。安装器对 SKILL.md frontmatter 完全透明——不解析 frontmatter，按文件整体 seed 到 `$ZAST_HOME/skills/`；filter / 翻译由 Desktop `electron/lib/skill-index.cjs` 和 Runner `tools/skills/skills_tool.py::skill_matches_platform` 各自做。

## 3. Tauri `bundle.resources` 嵌入 payload

`tauri.conf.json#bundle.resources` 包含以下条目（路径相对 `src-tauri/`）：

- `../payload/runner/` — Runner wheel（`zast_agent-*.whl`）+ `server.py`
- `../payload/skills` — symlink 到 `installer/skills/`
- `../payload/config.yaml` — Runner 默认配置（运行期释放到 `~/.zast/config.yaml`）
- `../payload/.staging.json` — build metadata（version / sha256 / host）
- `../payload/desktop/.gitkeep` — **占位符**。`scripts/build_client` 在 tauri build 前 patch 为实际 desktop artifact，build 后 restore
- `../payload/install.sh` / `../payload/install.ps1` — install 协议后端脚本（Tauri spawn 执行）
- `../payload/install.cmd` — Windows cmd.exe 兜底(dev 路径,生产 Tauri 不嵌入)

Tauri 进程把 bundle.resources 解压根通过 env var 传给 `install.{sh,ps1}`：

| Env var | 含义 |
|---------|------|
| `ZAST_BUNDLE_DIR` | bundle.resources 解压根 |
| `ZAST_BUNDLED_RUNNER_DIR` | `<bundle>/payload/runner/` |
| `ZAST_BUNDLED_DESKTOP_DIR` | `<bundle>/payload/desktop/` |
| `ZAST_BUNDLED_SKILLS_DIR` | `<bundle>/payload/skills/` |
| `ZAST_CONFIG_PATH` | `<bundle>/payload/config.yaml` |
| `ZAST_INSTALLER_FORMAT` | `dmg` / `nsis` / `AppImage` / `msi` / `zip` |

`install.{sh,ps1}` 6-stage protocol（`welcome` / `install-python` / `unpack-runner` / `unpack-desktop` / `install-skills` / `write-config`）从这些路径读 payload 并释放到 `$ZAST_HOME` 和平台标准位置。env var 优先、CLI arg 兜底（dev/test 用 `--bundled-*-dir`）。

## 4. 引导协议（bootstrap 流程）

`Zast-Setup.exe` 启动后调用 `installer/install.ps1 -Manifest`（macOS/Linux 是 `installer/install.sh`）拿 stage 列表，再逐 stage 用 `-Stage NAME -NonInteractive -Json` 执行。**6 个 stage**：`install-python` 检测/安装 Python 运行时（通过 uv，目标版本 3.13），其余 5 个 stage 专做 payload 释放——`install.{sh,ps1}` 不做构建（runner / desktop 都是预构建产物，由 Tauri `bundle.resources` 嵌入）。

完整规范在 `src-tauri/src/bootstrap.rs`，前端通过 Tauri `bootstrap` event channel 拿阶段进度。

## 5. 平台标准安装位置（已统一）

`bootstrap::desktop_install_root()` 给出 desktop 的平台-canonical 路径：

- **macOS**: `/Applications/Zast.app`（与 macOS 启动器约定一致）
- **Windows**: `%LOCALAPPDATA%\Programs\Zast\`（NSIS `/D=` 目标）
- **Linux**: `$ZAST_HOME/bin/`（AppImage 落地）

`install.sh` `Stage-UnpackDesktop` 的 macOS 分支用 `hdiutil attach` + `cp -R` + `xattr -cr` 把 `Zast.app` 放到 `/Applications`；Windows 分支用 `Start-Process /S /D=...` 调 NSIS 静默安装；Linux 分支 `cp +x` AppImage 到 `$ZAST_HOME/bin/` + 写 `~/.local/share/applications/zast.desktop`。


## 6. Install / macOS fast path

macOS 上若 `zast_is_installed() == true`（存在 `$ZAST_HOME/.zast-bootstrap-complete` 标记 + `/Applications/Zast.app/Contents/MacOS/Zast` 可启动），Tauri 直接 relaunch 桌面并退出——让 `/Applications/Zast.app` 兼任"首次启动走安装、之后是 launcher"。`--reinstall` / `--repair` 跳过 fast path，强制显示安装 UI。

`install_root` 对应 `$ZAST_HOME`。6-stage install 从 Tauri resources 释放 payload 到 `$ZAST_HOME/{bin,skills,config.yaml}`。`zast_is_installed` / `resolve_zast_desktop_exe` / `resolve_zast_desktop_app` 为无参函数（直接用 `zast_home()` + 平台标准路径）。

**Update 流完全由 desktop 负责**：内层 Electron desktop 通过 `electron-updater` 从 Backend `/api/update` 拉取预构建产物。`Zast-Setup` 只负责 install / uninstall / repair。

## 7. Install 脚本解析 —— 自包含，无网络

`install_script.rs::resolve` 的解析顺序：

1. **Dev shortcut**：`ZAST_SETUP_DEV_REPO_ROOT` env var 找到 `<root>/installer/install.{ps1,sh}` 直接用。dev 模式迭代用，无需重打包 Tauri。
2. **Bundled**（生产路径）：`app.path().resource_dir()/payload/install.{ps1,sh}` —— 由 Tauri `bundle.resources` 嵌入到 `Zast-Setup` 二进制内。**Installer binary is self-contained**——零网络依赖，零外部脚本下载。

Zast-Setup 二进制**不**下载 install 脚本（项目不放在 GitHub，所以无可下载源）。脚本版本 = installer build 版本。`scripts/build_client.{sh,ps1}` 在 `stage_payload()` 时把 `installer/install.{sh,ps1}` 硬链接 / 符号链接到 `installer/payload/`，Tauri 自动嵌入。

## 8. Python 运行时安装

Skills 中的 Python 脚本需要系统 Python 才能执行。`install-python` stage 通过 [uv](https://docs.astral.sh/uv/) 安装 Python：

1. 检查 `$ZAST_HOME/bin/uv.exe` 是否存在（托管 uv）
2. 不存在则从 `https://astral.sh/uv/install.ps1` 下载安装到 `$ZAST_HOME/bin/`
3. 用 `uv python install <version>` 安装 Python 到 uv 托管位置
4. 支持 Python 3.13（首选）+ 3.12/3.14/3.11 回退

当前 install 协议为 v2,包含 `install-python` stage(Protocol v1 仅含 5 个 payload 释放 stage)。Python 通过 uv 安装到 `$ZAST_HOME/bin/uv.exe`(Windows)或 `$ZAST_HOME/bin/uv`(POSIX)管理的位置,无需管理员权限。

## 9. Runner 安装布局

Runner 通过 `uv build --wheel` 构建为 `zast_agent-*.whl`。安装器在 `unpack-runner` stage 将 wheel 安装到 uv-managed venv：

```
$ZAST_HOME/runner/
├── server.py              # 从 tauri payload 复制（不随 wheel）
└── .venv/                 # uv venv（安装器创建）
    ├── bin/python          # POSIX
    ├── Scripts/python.exe  # Windows
    └── lib/.../site-packages/
        ├── tools/          # wheel 安装的 top-level package
        └── utils/          # wheel 安装的 top-level package
```

`unpack-runner` stage 流程：找 wheel → 复制 `server.py` → `uv venv` 创建 venv → `uv pip install <wheel>` 安装 wheel 及其依赖 → 冒烟 `import tools, utils` → 清理旧 `zast-runner{,.exe}`。

Desktop spawn 命令：`$ZAST_HOME/runner/.venv/{bin/python,Scripts/python.exe} $ZAST_HOME/runner/server.py --desktop-ws <ws-url>`。运行时路径 knob 仍是 `ZAST_HOME`。

## 重构行动项

Installer 在新定位下**基本保留**——install 协议、payload 释放、Python 运行时引导全不变。唯一新增考量是"蛋"阶段的形象来源。

- **保留（不动）**：6-stage install 协议（welcome / install-python / unpack-runner / unpack-desktop / install-skills / write-config）、Tauri 2 安装器二进制、payload 嵌入与释放、平台标准安装位置、macOS fast path、自包含零网络设计。
- **评估（产品设计决策）**：首装是否 seed 一个默认"蛋"形象资产到 `$ZAST_HOME/`，让 Desktop 在角色定义完成前离线渲染蛋；还是完全由 Desktop 内置默认蛋渲染（不经 installer）。倾向后者——蛋是 Desktop 内置默认形象，不需要 installer 参与，也避免引入 payload 与形象资产版本耦合。
- **无 payload 变更**：`skills/`、`config.yaml` payload 不受产品定位影响，install-skills / write-config stage 不变。
