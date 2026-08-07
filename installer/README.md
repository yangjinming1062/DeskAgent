# installer/

DeskAgent 安装器产品。本目录容纳 **Tauri 2 桌面程序**（`src/` + `src-tauri/`，产 `DeskAgent-Setup.exe` / `DeskAgent-Setup.app` / `deskagent-setup`）、它要释放的安装 payload（`skills/`、`config.yaml`、`voices/`）、以及 Tauri 进程 spawn 出来的 **install 协议后端**（`install.sh` / `install.ps1` / `install.cmd`）。首装完成释放 Client 后，Client 以"蛋"形态首次启动，进入 [DESIGN.md §3](../DESIGN.md) 的伙伴生命周期 onboarding。

## 1. 顶层模块解耦

**installer 是独立的顶级模块，禁止依赖 `client/`、`backend/`、`runner/` 的资源**——任何"借用"对方样式 / 组件 / 构建产物的做法都会让 Stage 2.x 的边界退化。`src/styles.css` 的设计 token、`src/components/button.tsx` 的 button variants、`fit-text` 工具类等**均为本地副本**；桌面端的设计系统演进时，installer 端独立决定是否跟随，不自动 link。

manifest 字段、`install.{ps1,sh}` 的 stage 名、JSON-RPC frame 等**协议级契约**可以跨模块引用——这些是公共接口，不是私有资源。

**Install 脚本为什么在 `installer/` 而不是 `scripts/`**：`install.{sh,ps1,cmd}` 是 Tauri 进程的 worker（被 `src-tauri/src/powershell.rs::run_script` spawn 出来干活），dev fallback 解析在 `DESKAGENT_SETUP_DEV_REPO_ROOT/installer/`，生产路径从 Tauri `bundle.resources` (`payload/install.{sh,ps1}`) 读取——与 installer 是 1:1 耦合关系，放在 `installer/` 让该模块自洽。`scripts/build_client.{sh,ps1}` 是跨 runner/client/installer 三模块的 repo 级 orchestrator，留在 `scripts/`。

**Installer binary is self-contained** —— install 脚本由 `scripts/build_client.{sh,ps1}` 在 `stage_payload()` 时硬链接 / 符号链接到 `installer/payload/`，Tauri 自动嵌入。`DeskAgent-Setup` 二进制运行时**零网络依赖**：不下载 install 脚本，不下载 payload（payload 都在 `bundle.resources` 里），不查更新（更新流走 backend `updates/` HTTP endpoint）。

**Client 启动后不做 install / uninstall** —— Client 启动时只连云端 Backend，不调 `install.ps1` / `DeskAgent-Setup --uninstall`。本模块（Tauri DeskAgent-Setup）拥有所有平台变更职责，desktop 是只读消费方。Electron 二进制自更新由 desktop 自己负责（`electron-updater` 从 Backend `/api/update` 拉取），详见 [client/README.md](../client/README.md)。

## 2. 同目录三类内容

- **`src/` + `src-tauri/`**：Tauri 2 桌面程序源码（Rust 后端 + React 19 前端），编译产物是签名好的安装器二进制。npm 包名 `@deskagent/installer`、cargo bin 名 `DeskAgent-Setup`、lib 名 `deskagent_bootstrap_lib`。
- **`install.sh` / `install.ps1` / `install.cmd`**：6-stage install 协议后端（welcome / install-python / unpack-runner / unpack-desktop / install-skills / write-config）。`install.cmd` 是 Windows 下 `install.ps1` 在 cmd.exe 不可用时的 dev fallback（`bundle.resources` 不嵌入）。完整规范见 §4。
- **`skills/`、`config.yaml`、`voices/`**：安装 payload——skills 由 `install-skills`、config 由 `write-config`、Piper voice 由 `unpack-runner` stage 分别读出来 seed 到 `$DESKAGENT_HOME`（路径契约详 [runner/README.md §Skills 系统](../runner/README.md)）。

**源位置 vs 嵌入位置**：`installer/skills/`（源，仓库随附）由 `scripts/build_client.{sh,ps1}` 的 `stage_payload()` 在 build 期建立 symlink 到 `installer/payload/skills/`，再由 Tauri `bundle.resources` 嵌入到 `DeskAgent-Setup` 二进制。install 脚本运行时读的是 `<bundle>/payload/skills/`（= §3 的 `DESKAGENT_BUNDLED_SKILLS_DIR`），不是源位置。

**Skills frontmatter**：`SKILL.md` 用 frontmatter 声明 `platforms: [macos | windows]`（缺省表示全平台；历史 `linux` 值仍被翻译表兼容，详见两边的过滤代码）。安装器对 frontmatter 完全透明——不解析，按文件整体 seed 到 `$DESKAGENT_HOME/skills/`；filter / 翻译由 Client `electron/lib/skill-index.cjs` 和 Runner `tools/skills/skills_tool.py::skill_matches_platform` 各自做（两套翻译表语义对齐，新增平台时需同步更新两边）。

## 3. Tauri `bundle.resources` 嵌入 payload

`tauri.conf.json#bundle.resources` 包含以下条目（路径相对 `src-tauri/`）：

- `../payload/runner/` — Runner wheel（`deskagent-agent-*.whl`）+ `server.py`
- `../payload/skills` — symlink 到 `installer/skills/`
- `../payload/voices` — 三份 Piper 离线 voice（见 §11）
- `../payload/config.yaml` — Runner 默认配置（运行期释放到 `~/.deskagent/config.yaml`）
- `../payload/.staging.json` — build metadata（version / sha256 / host）
- `../payload/client/.gitkeep` — **占位符**。`scripts/build_client` 在 tauri build 前 patch 为实际 client artifact，build 后 restore
- `../payload/install.sh` / `../payload/install.ps1` — install 协议后端脚本
- `../payload/install.cmd` — Windows cmd.exe 兜底（dev 路径，生产 Tauri 不嵌入）

Tauri 进程把 bundle.resources 解压根通过 env var 传给 `install.{sh,ps1}`：

| Env var | 含义 |
|---------|------|
| `DESKAGENT_BUNDLE_DIR` | bundle.resources 解压根 |
| `DESKAGENT_BUNDLED_RUNNER_DIR` | `<bundle>/payload/runner/` |
| `DESKAGENT_BUNDLED_DESKTOP_DIR` | `<bundle>/payload/client/` |
| `DESKAGENT_BUNDLED_SKILLS_DIR` | `<bundle>/payload/skills/` |
| `DESKAGENT_BUNDLED_VOICES_DIR` | `<bundle>/payload/voices/` |
| `DESKAGENT_CONFIG_PATH` | `<bundle>/payload/config.yaml` |
| `DESKAGENT_INSTALLER_FORMAT` | `dmg` / `nsis` / `msi` / `zip` |

`install.{sh,ps1}` 6-stage protocol 从这些路径读 payload 并释放到 `$DESKAGENT_HOME` 和平台标准位置。env var 优先、CLI arg 兜底（dev/test 用 `--bundled-*-dir`）。

## 4. 引导协议（bootstrap 流程）

`DeskAgent-Setup.exe` 启动后调用 `installer/install.ps1 -Manifest`（macOS 是 `installer/install.sh`）拿 stage 列表，再逐 stage 用 `-Stage NAME -NonInteractive -Json` 执行。**6 个 stage**：`welcome`（创建 `$DESKAGENT_HOME`）+ `install-python`（检测/安装 Python 运行时，通过 uv，目标 3.13）+ 3 个 payload 释放 stage（`unpack-runner` / `unpack-desktop` / `install-skills`）+ `write-config`（写配置 + 落 `.deskagent-bootstrap-complete` 标记）。`install.{sh,ps1}` 不做构建（runner / desktop 都是预构建产物，由 Tauri `bundle.resources` 嵌入）。

完整规范在 `src-tauri/src/bootstrap.rs`，前端通过 Tauri `bootstrap` event channel 拿阶段进度。当前 install 协议为 v2（含 `install-python` stage）。

## 5. 鉴权引导（auth bootstrap）

`welcome → auth → progress` 三段路由：`auth` 路由收集后端地址 + 用户名 + 密码，由 Tauri 命令 `verify_backend`（GET `/api/health`）和 `authenticate_backend`（POST `/api/user/login`，body 匹配 `backend/modules/auth/schemas.py::LoginRequest`）在 Rust 侧完成 HTTP。密码只存在于 POST body 与 React state，绝不落盘。

登录成功后写入的 one-shot 文件：
- 路径：`$DESKAGENT_HOME/agent-session-bootstrap.json`（`installer/src-tauri/src/paths.rs::deskagent_home()`）
- schema（`schema_version: 1`）：`{ base_url, token (raw jwt), token_expires_at, user, saved_at }`（serde 默认 snake_case 序列化，desktop 端按此字段名读取）
- POSIX 0600；Windows 由父目录 ACL 控制访问
- Client 启动时消费该文件：校验 schema + 调用 backend `POST /api/user/refresh` 验证 token；成功则原子重命名为 `.consumed` 并交给 `BackendSession::adoptSession` 走 `safeStorage` 落盘到 `agent-session.json`；任何失败（缺字段、解析错、refresh 401/网络失败）都静默删除文件并回落到未登录态。环境变量 `DESKAGENT_DESKTOP_BOOTSTRAP_SESSION` 覆盖消费路径。

## 6. 平台标准安装位置

`bootstrap::desktop_install_root()` 给出 desktop 的平台-canonical 路径：

- **macOS**: `/Applications/DeskAgent.app`（与 macOS 启动器约定一致）
- **Windows**: `%LOCALAPPDATA%\Programs\DeskAgent\`（NSIS `/D=` 目标）

`install.sh` `Stage-UnpackDesktop` 的 macOS 分支用 `hdiutil attach` + `cp -R` + `xattr -cr`；Windows 分支用 `Start-Process /S /D=...` 调 NSIS 静默安装。

## 7. macOS fast path

macOS 上若 `deskagent_is_installed() == true`——三条件全部满足：`$DESKAGENT_HOME/.deskagent-bootstrap-complete` 标记存在 + `/Applications/DeskAgent.app/Contents/MacOS/DeskAgent` 可启动 + Runner venv 健康（`runner_venv_is_healthy()` 探测依赖 import 链完整，broken venv 不满足）——Tauri 直接 relaunch 桌面并退出——让 `/Applications/DeskAgent.app` 兼任"首次启动走安装、之后是 launcher"。`--reinstall` / `--repair` 跳过 fast path，强制显示安装 UI。

## 8. Install 脚本解析 —— 自包含，无网络

`install_script.rs::resolve` 的解析顺序：

1. **Dev shortcut**：`DESKAGENT_SETUP_DEV_REPO_ROOT` env var 找到 `<root>/installer/install.{ps1,sh}` 直接用。dev 模式迭代用，无需重打包 Tauri。
2. **Bundled**（生产路径）：`app.path().resource_dir()/payload/install.{ps1,sh}` —— 由 Tauri `bundle.resources` 嵌入到 `DeskAgent-Setup` 二进制内。

DeskAgent-Setup 二进制**不**下载 install 脚本（项目不放在 GitHub，无可下载源）。脚本版本 = installer build 版本。

## 9. Python 运行时安装

Skills 中的 Python 脚本需要系统 Python。`install-python` stage 通过 [uv](https://docs.astral.sh/uv/) 安装：

1. 检查 `$DESKAGENT_HOME/bin/uv` 是否存在（托管 uv）
2. 不存在则从 `https://astral.sh/uv/install.ps1` 下载安装到 `$DESKAGENT_HOME/bin/`
3. 用 `uv python install <version>` 安装 Python 到 uv 托管位置
4. 支持 Python 3.13（首选）+ 3.12/3.14/3.11 回退

Python 通过 uv 安装到 uv 管理位置，无需管理员权限。

## 10. Runner 安装布局

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

Client spawn 命令：`$DESKAGENT_HOME/runner/.venv/{bin/python,Scripts/python.exe} $DESKAGENT_HOME/runner/server.py --desktop-ws <ws-url>`。运行时路径 knob 仍是 `DESKAGENT_HOME`。

## 已知限制

- **蛋形象不随 installer 分发**：角色定义完成前的"蛋"占位形象由 Client 内置默认渲染（`BrandMark` 组件），不经 installer seed payload。这避免了 payload 与形象资产版本耦合——installer 只负责代码与运行时分发，形象资产完全由 Backend 生成并下发（[ARCHITECTURE.md §6](../ARCHITECTURE.md)）。

## 11. 本地 TTS voice 打包

`installer/payload/voices/` 目录携带三份 Piper 离线 voice：

| voice id | 用途 | 大小 |
|----------|------|------|
| `zh_CN-huayan-medium` | 默认中文女声（onboarding 期间永远用） | ~60MB |
| `zh_CN-chaowen-medium` | 中文男声备用 | ~60MB |
| `en_US-amy-medium` | 用户从 voice catalog 选英文 voice 时用 | ~30MB |

`install.{sh,ps1}` 在 `unpack-runner` stage 用 content-based copy（看到 onnx 且同名 .onnx.json 存在才拷）把这三份都拷贝到 `$DESKAGENT_HOME/models/piper/`。**产品方向是"默认中文"**（[runner/README.md §音频工具](../runner/README.md#音频工具-stt--tts)）：onboarding 期间 silhouette 朗读中文问题、且本地 TTS 优先于云端时——立刻能听到中文 Piper 女声而不是 pyttsx3 的 Windows SAPI5 系统默认男声——所必须的兜底。

- **添加新 voice**：把 `<id>.onnx` 与 `<id>.onnx.json` 一起放入 `installer/payload/voices/`，并在 [`runner/tools/multimodal/audio/piper_runtime.py`](../runner/tools/multimodal/audio/piper_runtime.py) 的 `_BUNDLED_VOICES` 元组中注册 id（以及在 `ZH_DEFAULT_VOICE` / `ZH_MALE_DEFAULT_VOICE` / `EN_DEFAULT_VOICE` 常量里加一份 voice 引用，如果你想用某个常量名）。install 脚本用 content-based copy，不需要改 install 脚本。
- **Piper 缺失 voice 自动下载**：bundled 列表里没有的 Piper voice 会在首次 speak 时从 `huggingface.co/rhasspy/piper-voices` 拉一次（`ensure_voice_installed`）；失败回退 pyttsx3。bundled 路径本身就是为了让常见 onboarding 路径**永远不需要**走这条网络路径。
- **DESKAGENT_BUNDLED_VOICES_DIR** env var（or `--bundled-voices-dir` CLI arg）由 Tauri `bundle.resources` 解析到 `<bundle>/payload/voices/`，install 脚本读这个变量定位打包的 voice。
