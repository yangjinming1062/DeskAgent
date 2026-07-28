# Zast Agent

> 三模块 LLM Agent —— **Backend** 云端编排 + **Desktop** 本地枢纽 + **Runner** 隔离执行。

本地手脚分离的智能体系统:用户在桌面端与 LLM 对话,LLM 通过云端大脑调度,**终端、文件、浏览器、代码执行等与系统直接交互的能力**在用户本机隔离执行,云端不接触任何用户凭证或本地环境。

## 这是什么

Zast Agent 解决一个具体问题:**让 LLM 安全地"动手"操作用户本机**。

- **传统 LLM Agent** 要么只能云端回答(无执行力),要么把执行能力塞进 LLM SDK(用户凭证暴露给模型/云)
- **Zast Agent** 把"思考"放云端、把"动手"放本机,**用 WebSocket 协议 + JSON-RPC 2.0** 把两者解耦——Runner 进程不持有任何 Backend token,需要 LLM 时通过反向 RPC 借 Desktop 代为调用

## 模块架构

```
┌──────────────────────────┐
│  Backend (云端大脑)        │
│FastAPI + PostgreSQL + JWT│
│  • 多用户会话管理          │
│  • LLM 流式对话编排        │
│  • 系统提示词组装          │
│  • 云端工具(搜索/TTS/...)  │
│  • Cron 调度              │
│  Docker / Linux 容器       │
└────────────┬─────────────┘
             │  REST + WebSocket (JSON-RPC 2.0)
             │  唯一通道: WS /api/chat/ws?token=<jwt>
             ▼
┌──────────────────────────┐
│  Desktop (本地枢纽+UI)     │
│  Electron 42 + React 19   │
│  • JWT 加密落盘            │
│  • Runner Bridge 编排      │
│  • 17 个 IPC 命名空间      │
│  • 统一自更新(electron-   │
│    updater + 签名验证)     │
│  Windows/macOS/Linux 原生  │
└────────────┬─────────────┘
             │  本地 WebSocket (127.0.0.1:0 动态端口)
             │  JSON-RPC 2.0 + 反向 RPC (request_llm)
             ▼
┌──────────────────────────┐
│  Runner (本地手脚)         │
│  Python 3.13 (uv wheel)   │
│  • 47 个静态工具 + MCP    │
│  • 终端(6 个 _env_*.py:   │
│    base / file_sync /     │
│    local / docker / ssh / │
│    singularity)            │
│  • 浏览器(多后端)         │
│  • 文件 + 代码执行沙箱     │
│  • Skills 系统             │
│  Windows/macOS/Linux 原生  │
└──────────────────────────┘
```

**职责一句话**:
- **Backend = 大脑**:编排对话、组装提示词、调度云端工具、把本地工具调用下发给 Desktop
- **Desktop = 桥梁 + UI**:登录鉴权、桌面交互、把 Backend 的 tool.call 转发给本地 Runner、把 Runner 结果回传
- **Runner = 手脚**:执行本地工具,需要 LLM 时借 Desktop 代为调用 Backend

## 快速开始

### 后端 (Backend)

```bash
cd backend && docker compose up  # 监听 :8000，使用 PostgreSQL 作为数据库；默认管理员 zast/zast@admin123(生产前修改)
```

### 桌面 (Desktop,开发模式)

```bash
cd desktop && pnpm install && pnpm dev  # Vite :5174 + Electron;
# Backend URL 经 desktop/config.json（默认配置的本地127.0.0.1假设后端服务也在本机） 或 electron/config.cjs 配置
```

### Runner

由 installer 安装到 `$ZAST_HOME/runner/.venv`,desktop 启动时自动 spawn。dev 模式手动启动见 [runner/README.md §通信协议](runner/README.md)。

### 安装包 (Installer)

```bash
# Windows
pwsh scripts/build_client.ps1

# macOS / Linux
bash scripts/build_client.sh
```

产物:
- `release/Zast-Setup-{ver}.{exe|dmg|AppImage}` —— 首次安装 / 卸载 / repair
- `release/Zast-{ver}-update.zip` —— 运行期自更新 payload(走 `electron-updater` 通道)

发布运维:登录 `https://<your-backend>/admin/` → "版本管理" → 选 `release/Zast-{ver}-update.zip` → 上传并发布。

## 文档导航

根 README 只列"是什么 / 怎么跑 / 看哪里";架构设计、模块行为契约、跨切面安全/错误契约，请参阅：

| 想了解什么 | 看哪里 |
|-----------|--------|
| 项目总览 / 架构机制 / 通信协议与不变量 | [design.md](design.md) |
| Backend 模块结构与实现 | [backend/README.md](backend/README.md) |
| Runner 模块结构与实现 | [runner/README.md](runner/README.md) |
| Desktop 模块结构与实现 | [desktop/README.md](desktop/README.md) |
| Installer 模块结构与协议 | [installer/README.md](installer/README.md) |
| 构建 / 测试 / 发布脚本 | [scripts/README.md](scripts/README.md) |
| 仓库级 AI 协作规范 | [CLAUDE.md](CLAUDE.md) |

## 平台支持

| 模块 | 部署目标 | 兼容性要求 |
|------|---------|-----------|
| Backend | Linux (Docker 容器) | 仅 Linux,无 Windows 兼容要求 |
| Runner | Windows / macOS / Linux 原生 | Windows 是已知风险面(见 [runner/README.md §已知限制](runner/README.md)) |
| Desktop | Windows / macOS / Linux 原生 | Windows 兼容性是已知风险面(见 [desktop/README.md §已知限制](desktop/README.md)) |
| Installer | Windows / macOS / Linux 原生 | Tauri 2 cross-platform;当前 install 协议 v2(含 install-python stage) |

## 当前信任模型

- **Runner 零凭证**:不持有 Backend token;需借 LLM 时通过反向 RPC 经 Desktop 代调 `POST /api/llm/completion`
- **JWT 加密落盘**:Electron `safeStorage` 跨平台统一(DPAPI / Keychain / libsecret)
- **自更新签名**:Electron 二进制走 `electron-updater` RSA;Python runner wheel 走 `scripts/secrets/update.pub` RSA + SHA-512 双重校验
- **API key fingerprinting**:`GET /api/user/model-config` 只返回 `sk-…XX` 形式的 fingerprint,原始 key 永不离开 Backend
- **国际化**:Desktop 渲染层支持 en / zh 切换,默认 zh(Settings → Appearance → Language)
- **Skills 走单独通道**:由 installer 在首装 seed,desktop 自更新不下载

## 开发约定

- 改实现方案 / 架构 / 导出 → **同提交**同步该模块及所有受影响父级 `CLAUDE.md`(完整规范见 [CLAUDE.md](CLAUDE.md))
- 文档用中文,不写"X 不再 Y / 现在改为 Z"等变化叙事;git log / blame 负责历史,CLAUDE.md 描述当下
- 提交前:`pwsh scripts/build_client.ps1` 全链路
