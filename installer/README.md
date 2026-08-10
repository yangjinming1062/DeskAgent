# Installer

DeskAgent 安装器产品。本目录容纳 **Tauri 2 桌面程序**（`src/` + `src-tauri/`，产 `DeskAgent-Setup.exe` / `DeskAgent-Setup.app` / `deskagent-setup`）、它要释放的安装 payload（`skills/`、`config.yaml`、`voices/`）、以及 Tauri 进程 spawn 出来的 **install 协议后端**（`install.sh` / `install.ps1` / `install.cmd`）。首装完成释放 Client 后，Client 以"蛋"形态首次启动，进入 [DESIGN.md §5](../DESIGN.md) 的伙伴生命周期 onboarding。

## 1. 职责与边界

**职责**：
- 首装引导 Python 运行时（uv-managed）+ 释放 Runner wheel / Client 二进制 / Skills / config 到平台标准路径
- 用户登录 → 写 auth bootstrap one-shot → 退出，Client 启动接管
- macOS fast path：`/Applications/DeskAgent.app` 兼任"首次启动走安装、之后是 launcher"
- 自包含、无网络：install 脚本与 payload 全部嵌入 Tauri `bundle.resources`

**不**做：
- **不下载 install 脚本**——脚本由 Tauri `bundle.resources` 嵌入，版本 = installer build 版本
- **不下载 payload**——payload 都在 `bundle.resources` 里
- **不查更新**——更新流走 Backend `updates/` HTTP endpoint（[PROTOCOL.md §5.6](../PROTOCOL.md)）
- **不依赖 `client/`、`backend/`、`runner/` 的资源**——任何"借用"对方样式 / 组件 / 构建产物的做法都会让 Stage 2.x 的边界退化；`src/styles.css` 的设计 token、`src/components/button.tsx` 的 button variants 等**均为本地副本**
- **不分发蛋形象资产**——蛋形象由 Client 内置默认渲染，安装期由 `progress.tsx` 矢量蛋组件 (`<Egg>`) + 6 段同心光环 (`<Halo>`) 提供破壳进度叙事，形象资产完全由 Backend 生成并下发（[ARCHITECTURE.md §6](../ARCHITECTURE.md)）

架构层定位见 [ARCHITECTURE.md §1 / §2](../ARCHITECTURE.md)；Client 接力见 [client/README.md §5](../client/README.md)（auth bootstrap one-shot）。

## 2. 设计意图

- **Installer binary self-contained**：install 脚本与 payload 全部嵌入 Tauri `bundle.resources`，运行期零网络依赖。**为什么不下载**：项目不放在 GitHub，无可下载源；脚本版本 = installer build 版本。
- **Install 协议后端是 worker（6-stage protocol）**：`install.{sh,ps1}` 是被 Tauri 进程 spawn 出来干活的 worker，不是 orchestrator——Tauri 是 orchestrator，6 个 stage（`welcome` / `install-python` / `unpack-runner` / `unpack-desktop` / `install-skills` / `write-config`）按序执行。Tauri 进程经 env var 把 `bundle.resources` 解压根传给 install 脚本。
- **Install 脚本在 `installer/` 而非 `scripts/`**：1:1 耦合关系——`scripts/build_client.{sh,ps1}` 在 `stage_payload()` 时硬链接 / 符号链接到 `installer/payload/`，Tauri 自动嵌入。`scripts/build_client.{sh,ps1}` 才是跨 runner/client/installer 三模块的 repo 级 orchestrator。
- **uv-managed venv + system-Python-free**：通过 [uv](https://docs.astral.sh/uv/) 安装 Python 到 uv 托管位置（无需管理员权限），venv 创建在 `$DESKAGENT_HOME/runner/.venv`。**为什么不依赖 system Python**：不同 OS 自带 Python 版本差异大，依赖系统 Python 会让 install 兼容性变成无尽测试矩阵。
- **macOS fast path**：`/Applications/DeskAgent.app` 兼任"首次启动走安装、之后是 launcher"。三条件全满足（`$DESKAGENT_HOME/.deskagent-bootstrap-complete` + `/Applications/DeskAgent.app/.../DeskAgent` 可启动 + Runner venv 健康）时 Tauri 直接 relaunch 桌面并退出；`--reinstall` / `--repair` 跳过 fast path。
- **auth bootstrap one-shot**（与 Client 协作）：登录成功后写 `$DESKAGENT_HOME/agent-session-bootstrap.json`（schema_version=1，POSIX 0600；Windows 由父目录 ACL 控制）；Client 启动时消费、消费后原子重命名为 `.consumed` 并通过 `POST /api/user/refresh` 校验 token。任何失败静默删文件回退到未登录态。**为什么不直接落盘到 `agent-session.json`**：installer 是临时进程，不能保证 safeStorage 跨平台一致；让 Client 启动时统一走 safeStorage 落盘更安全。
- **Piper voice 三份打包**：`zh_CN-huayan-medium` / `zh_CN-chaowen-medium` / `en_US-amy-medium` 嵌入 `installer/payload/voices/`，content-based copy 到 `$DESKAGENT_HOME/models/piper/`。**为什么必须打包**：onboarding 期间 silhouette 朗读中文问题、且本地 TTS 优先于云端时——立刻能听到中文 Piper 女声而不是 pyttsx3 的 Windows SAPI5 系统默认男声——必须的兜底，避免首装断网时连 onboarding 都做不了。
- **Skills frontmatter 安装器透明**：不解析 frontmatter，按文件整体 seed 到 `$DESKAGENT_HOME/skills/`；filter / 翻译由 Client `electron/lib/skill-index.cjs` 和 Runner `tools/skills/skills_tool.py::skill_matches_platform` 各自做（两套翻译表语义对齐，新增平台时需同步更新两边）。

## 3. 架构地图

```
installer/
├── src/                   # React 19 前端（登录 / 进度 UI）
├── src-tauri/             # Rust 后端 + Tauri 配置
│   ├── src/
│   │   ├── bootstrap.rs   # 6-stage protocol orchestrator
│   │   ├── powershell.rs  # spawn install 脚本的 worker
│   │   ├── paths.rs       # $DESKAGENT_HOME / desktop_install_root()
│   │   └── install_script.rs  # 脚本解析（Dev shortcut / Bundled）
│   └── tauri.conf.json    # bundle.resources 配置
├── install.sh             # POSIX install 协议后端
├── install.ps1            # Windows install 协议后端
├── install.cmd            # Windows cmd.exe 兜底（dev 路径）
├── skills/                # 安装 payload: skills seed
├── payload/               # build 期 symlink 到 skills/ + build 产物
│   ├── runner/            # Runner wheel + server.py
│   ├── client/            # Client build 产物
│   ├── voices/            # Piper offline voices
│   ├── config.yaml        # Runner 默认配置
│   └── install.{sh,ps1,cmd}
└── .staging.json          # build metadata（version / sha256 / host）
```

依赖方向：`src-tauri/` ← `src/`（Tauri 命令）；`install.{sh,ps1,cmd}` 是独立 worker，被 `src-tauri/` 经 `powershell.rs::run_script` spawn；`scripts/build_client.{sh,ps1}` 是跨模块 orchestrator，build 时 stage payload 到 `installer/payload/`。

## 4. 关键设计决策

- **uv-managed venv 而非 system Python**：通过 `uv` 安装 Python 到 uv 托管位置，venv 创建在 `$DESKAGENT_HOME/runner/.venv`。**为什么不依赖 system Python**：macOS 自带 Python 3（不会破坏）、Windows 默认无 Python、Linux 发行版差异大；依赖系统 Python 会让 install 兼容性变成无尽测试矩阵。**为什么不直接用 conda/venv**：uv 单一二进制 + 跨平台 + 5× 速度 + 内置 Python 安装，是当前最佳选择。
- **install 协议 6 stage 拆分**：`welcome` → `install-python` → `unpack-runner` → `unpack-desktop` → `install-skills` → `write-config`。**为什么不一次性 `pip install` + `cp` + `write config`**：分阶段让 Tauri 可以单独 retry / 单独回滚单 stage；崩溃可以从断点恢复；进度条粒度更细。
- **uv pip install 失败后自动用镜像重试**：`DESKAGENT_PYPI_INDEX_URL` / `PIP_INDEX_URL` 环境变量优先，缺省阿里云镜像 `https://mirrors.aliyun.com/pypi/simple/`。**为什么不内置 pip mirror 配置**：用户自托管 / 企业代理场景下可能需要私有 index；env var 优先 + 兜底镜像覆盖大多数情况。
- **macOS fast path**（前述）：让 `/Applications/DeskAgent.app` 兼任"首次启动走安装、之后是 launcher"——减少启动时的 UI 闪烁、避免每次启动都进入安装器界面。**代价**：fast path 跳过任何"组件已损坏需要 repair"的检测；用户需手动 `--repair`。
- **`--reinstall` / `--repair` 跳过 fast path**：显式覆盖 fast path 进入完整 UI，让用户能修复已损坏的 install。**为什么不复用 fast path**：fast path 的本意是"已 install 一切正常，无需打扰用户"。
- **Piper voice `content-based copy`**：只有当目标目录同时缺 `<id>.onnx` 和 `<id>.onnx.json` 时才拷贝，避免每次启动 install 都重写大文件。**为什么不每次都覆盖**：60MB × 3 个 voice × 每次 reinstall 都是几百 MB IO；content-based copy 让 reinstall 几乎零 IO。
- **`auth bootstrap` schema_version=1 + 原子重命名为 `.consumed`**：原子重命名避免 Client 在读取过程中文件被改动；`.consumed` 后缀既能让人工排查"已消费"状态，也保证不被二次消费。**为什么不让 Client 写回 `agent-session.json`**：Client 启动时要 refresh token 验真，installer 写入的 token 可能已过期 / baseUrl 已变化（用户换 Backend）。
- **`DeskAgent-Setup --uninstall` 而非 Client 触发 uninstall**：所有平台变更职责集中在 Installer；Client 启动时只连云端 Backend，不调 `install.ps1` / `DeskAgent-Setup --uninstall`。**为什么不让 Client 自卸载**：卸载是 OS 级变更（移除 `$DESKAGENT_HOME` / `/Applications/DeskAgent.app` / 注册表 / 计划任务），Client 无足够权限（macOS 需要 sudo）。

## 5. 与外部的契约

| 契约 | 方向 | 在哪定义 |
|------|------|---------|
| 资产 URL 5 分钟 HMAC 签名 | — | 不适用（Installer 不消费 Backend 签名资产） |
| 错误信封 | — | 不适用 |
| API Key 永不离后端 | — | Installer 不涉及 LLM 凭证 |
| **Install 协议 v2**（含 `install-python` stage）6 stage 流程 | Tauri 进程 ↔ install 脚本 | 本模块独有 + `src-tauri/src/bootstrap.rs` |
| **Tauri `bundle.resources` 嵌入清单**（runner / client / voices / config / skills / install scripts） | Tauri build 期 ↔ install 脚本运行期 | 本模块独有 + `src-tauri/tauri.conf.json` |
| **`DESKAGENT_BUNDLE_DIR` / `DESKAGLED_RUNNER_DIR` / `DESKAGENT_BUNDLED_DESKTOP_DIR` / `DESKAGENT_BUNDLED_SKILLS_DIR` / `DESKAGENT_BUNDLED_VOICES_DIR` / `DESKAGENT_CONFIG_PATH` / `DESKAGENT_INSTALLER_FORMAT` env var 契约** | Tauri → install 脚本 | 本模块独有 |
| **`$DESKAGENT_HOME` 路径**（含 platform canonical） | Tauri → install 脚本 → Client / Runner | 本模块独有 + [runner/README.md §1](../runner/README.md) + [client/README.md §2](../client/README.md) |
| **`agent-session-bootstrap.json` schema_version=1 one-shot** | Installer → Client 消费 | [client/README.md §4](../client/README.md)（install bootstrap）+ [PROTOCOL.md §5.3](../PROTOCOL.md)（safeStorage） |
| **`desktop_install_root()` platform canonical 路径** | Tauri | 本模块独有（macOS `/Applications/DeskAgent.app` / Windows `%LOCALAPPDATA%\Programs\DeskAgent\`） |
| **Runner 安装布局** `$DESKAGENT_HOME/runner/.venv/` + spawn 命令 | Installer → Client | [runner/README.md §1](../runner/README.md) + 本 README §3 |
| **Piper voice 三份打包 + `_BUNDLED_VOICES` 元组** + **`ZH_DEFAULT_VOICE` / `ZH_MALE_DEFAULT_VOICE` / `EN_DEFAULT_VOICE`** 常量 | Installer → Runner | [runner/tools/multimodal/audio/piper_runtime.py](../runner/tools/multimodal/audio/piper_runtime.py) |
| **Piper voice `content-based copy` 规则** | Installer | 本 README §4 |
| **Skills frontmatter `platforms: [macos \| windows]` 过滤 + 历史 `linux` 值翻译表** | Installer → Client + Runner | 本 README §2 + 两端各自过滤代码 |
| **`DESKAGENT_SETUP_DEV_REPO_ROOT` dev shortcut**（dev 模式迭代无需重打包 Tauri） | dev 工作流 | 本 README §4 |

## 6. 已知限制

| 限制 | 说明 |
|------|------|
| **蛋形象不随 installer 分发** | 角色定义完成前的"蛋"占位形象由 Client 内置默认渲染（`BrandMark` 组件），不经 installer seed payload；避免 payload 与形象资产版本耦合 |
| **Skills frontmatter 翻译表需双端同步** | `client/electron/lib/skill-index.cjs` 与 `runner/tools/skills/skills_tool.py::skill_matches_platform` 各自实现平台过滤（macos / windows），新增平台时需同步更新两套翻译表 |
| **`install.cmd` 仅 dev 路径** | Windows cmd.exe 不可用时兜底；`bundle.resources` 不嵌入，仅 dev fallback |
| **macOS fast path 不会自我检测损坏** | 三条件全满足时跳过 UI；用户需手动 `--repair` 走完整 reinstall |
| **uv pip install 失败回退到阿里云镜像** | 网络隔离的企业用户需设 `DESKAGENT_PYPI_INDEX_URL` / `PIP_INDEX_URL` |
| **新 Piper voice 需手动注册** | 把 `<id>.onnx` 与 `<id>.onnx.json` 放入 `installer/payload/voices/`，并在 `runner/tools/multimodal/audio/piper_runtime.py` 的 `_BUNDLED_VOICES` 元组中注册 id |
| **ZIP 安装格式 desktop 路径回退** | `DESKAGENT_INSTALLER_FORMAT=zip` 解压到 `$DESKAGENT_HOME` 时 desktop 不在 canonical 路径；`resolve_deskagent_desktop_exe` 额外回退 `$DESKAGENT_HOME/apps/DeskAgent/DeskAgent.exe` |
| **Self-contained install 脚本不下载更新** | DeskAgent-Setup 二进制**不**下载 install 脚本（项目不放在 GitHub，无可下载源）；脚本版本 = installer build 版本 |