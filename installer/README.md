# Installer

SpiritAgent 安装器产品。本目录容纳 Tauri 2 桌面程序、待释放的安装 payload（skills）、以及 Tauri 进程启动的安装协议脚本。首装完成释放客户端后，客户端以"蛋"形态首次启动，进入 [DESIGN.md §5](../DESIGN.md) 的伙伴生命周期 onboarding；构建入口与产物见 [scripts/README.md](../scripts/README.md)。

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
- **Install 脚本在 `installer/` 而非 `scripts/`**：安装协议与 Tauri 程序 1:1 耦合，必须随安装器版本一起演进；仓库级构建编排归 [scripts/README.md](../scripts/README.md)。
- **uv-managed venv，不依赖 system Python**：通过 [uv](https://docs.astral.sh/uv/) 安装 Python 到 uv 托管位置（无需管理员权限），venv 创建在 `$SPIRITAGENT_HOME/runner/.venv`。为什么不依赖 system Python：不同 OS 自带 Python 版本差异大，依赖系统 Python 会让 install 兼容性变成无尽测试矩阵。
- **auth bootstrap one-shot**（与客户端协作）：登录成功后写一次性 bootstrap 文件（POSIX 0600；Windows 由父目录 ACL 控制）；客户端启动时消费、消费后原子重命名标记并到后端校验 token，任何失败静默删文件回退未登录态。为什么不直接写客户端的会话文件：installer 是临时进程，不能保证 safeStorage 跨平台一致；让客户端启动时统一走 safeStorage 落盘更安全。
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
│   └── install.{sh,ps1,cmd}
```

依赖方向：`src-tauri/` ← `src/`（Tauri 命令）；`install.{sh,ps1,cmd}` 是独立 worker，被 `src-tauri/` spawn。仓库级构建入口见 [scripts/README.md](../scripts/README.md)。

## 4. 关键设计决策

- **install 协议 6 stage 拆分**：welcome → install-python → unpack-runner → unpack-desktop → install-skills → finalize；finalize 写完成 marker，Runner 配置由客户端经 WS 协议推送。为什么一次性安装 + 拷贝不行：分阶段让 Tauri 可以单独 retry / 单独回滚单 stage；崩溃可以从断点恢复；进度条粒度更细。
- **安装脚本结构化帧经 NDJSON 哨兵前缀提取**：脚本 stdout 混杂第三方工具输出（pip/uv/robocopy），阶段结果与 manifest 均带显式哨兵前缀单行输出，避免盲目反扫与 JSON 解析歧义。
- **macOS 自拷贝签名显式判定防静默降级**：自拷贝到 `$SPIRITAGENT_HOME` 后通过 `codesign -d` 区分未签名（补 ad-hoc 签名以支持 Apple Silicon 运行）、损坏 ad-hoc 签名（重新签）与权威证书签名（严格校验，严禁静默降级覆盖为 ad-hoc 签名）。
- **uv pip install 失败后自动用镜像重试**：`SPIRITAGENT_PYPI_INDEX_URL` / `PIP_INDEX_URL` 环境变量优先，缺省阿里云镜像。为什么不内置 pip mirror 配置：用户自托管 / 企业代理场景可能需要私有 index；env var 优先 + 兜底镜像覆盖大多数情况。
- **macOS fast path 完整性自检与自愈**：让 `/Applications/SpiritAgent.app` 兼任"首次启动走安装、之后是 launcher"——减少启动时的 UI 闪烁。已安装判定不止看 marker，还深度校验 Runner venv 核心依赖可导入，检测到损坏自动回退完整安装/修复流程；`--reinstall` / `--repair` 显式跳过 fast path。
- **卸载由安装器而非客户端触发**：所有平台变更职责集中在 Installer；客户端启动时只连云端后端，不调卸载流程。为什么不让客户端自卸载：卸载是 OS 级变更（移除安装目录 / 应用目录 / 注册表 / 计划任务），客户端无足够权限（macOS 需要 sudo）。
- **ZIP 格式解压路径自适应回退**：`SPIRITAGENT_INSTALLER_FORMAT=zip` 解压到 `$SPIRITAGENT_HOME` 时客户端不在 canonical 路径，额外回退 `$SPIRITAGENT_HOME/apps/SpiritAgent/SpiritAgent.exe`。
- **`install.cmd` 开发期辅助脚本**：Windows cmd.exe 仅供本地开发调试兜底，生产环境 `bundle.resources` 不嵌入。

## 5. 与外部的契约

| 契约 | 方向 | 在哪定义 |
|------|------|---------|
| 安装协议、stages 与进度语义 | Tauri ↔ 安装脚本 | 本 README §4 |
| Payload 嵌入清单与安装根路径 | Tauri ↔ 安装脚本 → 客户端 / Runner | 本 README §3 / §4 |
| Runner venv 布局与启动命令 | Installer → 客户端 | 本 README §2 / §3（Runner 入口见 [runner/README.md §3](../runner/README.md)） |
| Skills 平台过滤 | Installer → 客户端 + Runner | 本 README §2 |

## 6. 已知限制

| 限制 | 说明 |
|------|------|
| **默认镜像可能不符合企业网络策略** | 部署方需显式配置私有 PyPI index 环境变量 |
