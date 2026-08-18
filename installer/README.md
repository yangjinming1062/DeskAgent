# Installer

SpiritAgent 安装器产品。本目录容纳 **Tauri 2 桌面程序**（`src/` + `src-tauri/`，产 `SpiritAgent-Setup.exe` / `SpiritAgent-Setup.app` / `spiritagent-setup`）、它要释放的安装 payload（`skills/`、`voices/`）、以及 Tauri 进程 spawn 出来的 **install 协议后端**（`install.sh` / `install.ps1` / `install.cmd`）。首装完成释放客户端后，客户端以"蛋"形态首次启动，进入 [DESIGN.md §5](../DESIGN.md) 的伙伴生命周期 onboarding。

## 1. 职责与边界

**职责**：
- 首装引导 Python 运行时（uv-managed）+ 释放 Runner wheel / 客户端二进制 / Skills 到平台标准路径
- 安装完成即退出，客户端启动后由用户在伙伴窗口内输入激活码完成认证
- macOS fast path：`/Applications/SpiritAgent.app` 兼任"首次启动走安装、之后是 launcher"
- 自包含、无网络：install 脚本与 payload 全部嵌入 Tauri `bundle.resources`

**不**做：
- **不下载 install 脚本**——脚本由 `bundle.resources` 嵌入，版本 = installer build 版本
- **不下载 payload**——payload 都在 `bundle.resources` 里
- **不查更新**——更新流走后端更新端点（[PROTOCOL.md §5.5](../PROTOCOL.md)）
- **不依赖 `client/`、`backend/`、`runner/` 的资源**——任何"借用"对方样式 / 组件 / 构建产物的做法都会让 Stage 2.x 的边界退化；前端设计 token、按钮变体等**均为本地副本**
- **不分发蛋形象资产**——蛋形象由客户端内置默认渲染，安装期由矢量蛋组件 + 同心光环提供破壳进度叙事，形象资产完全由后端生成并下发（[ARCHITECTURE.md §6](../ARCHITECTURE.md)）

架构层定位见 [ARCHITECTURE.md §1 / §2](../ARCHITECTURE.md)；客户端认证流程见 [client/README.md §5](../client/README.md)（激活码制）。

## 2. 设计意图

- **安装器自包含**：install 脚本与 payload 全部嵌入 Tauri `bundle.resources`，运行期零网络依赖。为什么不下载：项目不放在 GitHub，无可下载源；脚本版本 = installer build 版本。
- **Install 协议后端是 worker（6-stage protocol）**：install 脚本是被 Tauri 进程 spawn 出来干活的 worker，不是 orchestrator——Tauri 是 orchestrator，6 个 stage（welcome / install-python / unpack-runner / unpack-desktop / install-skills / finalize）按序执行。Tauri 进程经 env var 把资源解压根传给 install 脚本。
- **Install 脚本在 `installer/` 而非 `scripts/`**：1:1 耦合关系——构建脚本在 stage payload 时硬链接 / 符号链接到 `installer/payload/`，Tauri 自动嵌入。`scripts/build_client.{sh,ps1}` 才是跨三模块的仓库级 orchestrator。
- **uv-managed venv，不依赖 system Python**：通过 [uv](https://docs.astral.sh/uv/) 安装 Python 到 uv 托管位置（无需管理员权限），venv 创建在 `$SPIRITAGENT_HOME/runner/.venv`。为什么不依赖 system Python：不同 OS 自带 Python 版本差异大，依赖系统 Python 会让 install 兼容性变成无尽测试矩阵。
- **auth bootstrap one-shot**（与客户端协作）：登录成功后写一次性 bootstrap 文件（POSIX 0600；Windows 由父目录 ACL 控制）；客户端启动时消费、消费后原子重命名标记并到后端校验 token，任何失败静默删文件回退未登录态。为什么不直接写客户端的会话文件：installer 是临时进程，不能保证 safeStorage 跨平台一致；让客户端启动时统一走 safeStorage 落盘更安全。
- **Piper voice 三份打包**：`zh_CN-huayan-medium` / `zh_CN-chaowen-medium` / `en_US-amy-medium` 嵌入 payload，按内容判定后拷贝到本地模型目录。为什么必须打包：onboarding 期间本地 TTS 优先时立刻能听到中文女声，而不是系统默认男声——避免首装断网时连 onboarding 都做不了。
- **Skills frontmatter 安装器透明**：不解析 frontmatter，按文件整体 seed 到本地 Skills 目录；过滤 / 翻译由客户端与 Runner 各自做（两套翻译表语义对齐，新增平台时需同步更新两边）。

## 3. 架构地图

```
installer/
├── src/                   # React 19 前端（欢迎 / 进度 UI）
├── src-tauri/             # Rust 后端 + Tauri 配置
│   ├── src/
│   │   ├── bootstrap.rs   # 6-stage protocol orchestrator
│   │   ├── powershell.rs  # spawn install 脚本的 worker
│   │   ├── paths.rs       # $SPIRITAGENT_HOME / desktop_install_root()
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
│   └── install.{sh,ps1,cmd}
```

依赖方向：`src-tauri/` ← `src/`（Tauri 命令）；`install.{sh,ps1,cmd}` 是独立 worker，被 `src-tauri/` spawn；`scripts/build_client.{sh,ps1}` 是跨模块 orchestrator，build 时 stage payload 到 `installer/payload/`。

## 4. 关键设计决策

- **install 协议 6 stage 拆分**：welcome → install-python → unpack-runner → unpack-desktop → install-skills → finalize；finalize 写完成 marker（Runner 配置由客户端经 WS 协议推送，不再经文件）。为什么一次性安装 + 拷贝不行：分阶段让 Tauri 可以单独 retry / 单独回滚单 stage；崩溃可以从断点恢复；进度条粒度更细。
- **uv pip install 失败后自动用镜像重试**：`SPIRITAGENT_PYPI_INDEX_URL` / `PIP_INDEX_URL` 环境变量优先，缺省阿里云镜像。为什么不内置 pip mirror 配置：用户自托管 / 企业代理场景可能需要私有 index；env var 优先 + 兜底镜像覆盖大多数情况。
- **macOS fast path 完整性自检与自愈**：让 `/Applications/SpiritAgent.app` 兼任"首次启动走安装、之后是 launcher"——减少启动时的 UI 闪烁。已安装判定不止看 marker，还深度校验 Runner venv 核心依赖可导入，检测到损坏自动回退完整安装/修复流程；`--reinstall` / `--repair` 显式跳过 fast path。
- **Piper voice 按内容判定拷贝**：只有当目标目录同时缺 onnx 模型与配套 json 时才拷贝，避免每次启动都重写大文件。为什么不是每次覆盖：60MB × 3 个 voice × 每次 reinstall 都是几百 MB IO；按内容判定让 reinstall 几乎零 IO。
- **卸载由安装器而非客户端触发**：所有平台变更职责集中在 Installer；客户端启动时只连云端后端，不调卸载流程。为什么不让客户端自卸载：卸载是 OS 级变更（移除安装目录 / 应用目录 / 注册表 / 计划任务），客户端无足够权限（macOS 需要 sudo）。
- **ZIP 格式解压路径自适应回退**：`SPIRITAGENT_INSTALLER_FORMAT=zip` 解压到 `$SPIRITAGENT_HOME` 时客户端不在 canonical 路径，额外回退 `$SPIRITAGENT_HOME/apps/SpiritAgent/SpiritAgent.exe`。
- **`install.cmd` 开发期辅助脚本**：Windows cmd.exe 仅供本地开发调试兜底，生产环境 `bundle.resources` 不嵌入。

## 5. 与外部的契约

| 契约 | 方向 | 在哪定义 |
|------|------|---------|
| 资产 URL 5 分钟 HMAC 签名 | — | 不适用（Installer 不消费后端签名资产） |
| 错误信封 | — | 不适用 |
| API Key 永不离后端 | — | Installer 不涉及 LLM 凭证 |
| **Install 协议 v2**（含 `install-python` stage）6 stage 流程 | Tauri 进程 ↔ install 脚本 | 本模块独有 + `src-tauri/src/bootstrap.rs` |
| **Tauri `bundle.resources` 嵌入清单**（runner / client / voices / skills / install scripts） | Tauri build 期 ↔ install 脚本运行期 | 本模块独有 + `src-tauri/tauri.conf.json` |
| **`SPIRITAGENT_BUNDLE_DIR` / `DESKAGLED_RUNNER_DIR` / `SPIRITAGENT_BUNDLED_DESKTOP_DIR` / `SPIRITAGENT_BUNDLED_SKILLS_DIR` / `SPIRITAGENT_BUNDLED_VOICES_DIR` / `SPIRITAGENT_INSTALLER_FORMAT` env var 契约** | Tauri → install 脚本 | 本模块独有 |
| **`$SPIRITAGENT_HOME` 路径**（含 platform canonical） | Tauri → install 脚本 → 客户端 / Runner | 本模块独有 + [runner/README.md §1](../runner/README.md) + [client/README.md §2](../client/README.md) |
| **`desktop_install_root()` platform canonical 路径** | Tauri | 本模块独有（macOS `/Applications/SpiritAgent.app` / Windows `%LOCALAPPDATA%\Programs\SpiritAgent\`） |
| **Runner 安装布局** `$SPIRITAGENT_HOME/runner/.venv/` + spawn 命令 | Installer → 客户端 | [runner/README.md §1](../runner/README.md) + 本 README §3 |
| **Piper voice 三份打包 + 默认音色常量**（中文女声 / 中文男声 / 英文） | Installer → Runner | [runner/tools/multimodal/audio/piper_runtime.py](../runner/tools/multimodal/audio/piper_runtime.py) |
| **Piper voice 按内容判定拷贝规则** | Installer | 本 README §4 |
| **Skills frontmatter `platforms: [macos \| windows]` 过滤 + 历史 `linux` 值翻译表** | Installer → 客户端 + Runner | 本 README §2 + 两端各自过滤代码 |
| **`SPIRITAGENT_SETUP_DEV_REPO_ROOT` dev shortcut**（dev 模式迭代无需重打包 Tauri） | dev 工作流 | 本 README §4 |

## 6. 已知限制

| 限制 | 说明 |
|------|------|
| **uv pip install 失败回退到阿里云镜像** | 网络隔离的企业用户需设 `SPIRITAGENT_PYPI_INDEX_URL` / `PIP_INDEX_URL` |
