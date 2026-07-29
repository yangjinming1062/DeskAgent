# DeskAgent

> 定制化陪伴型桌面伙伴 —— **Backend** 云端承载人格与形象，**Desktop** 本地渲染伙伴并中转，**Runner** 隔离执行本机操作。

DeskAgent 是一个**根据用户描述定制的、具有专属形象的陪伴型桌面伙伴**。用户首次安装时以一颗"蛋"的形态见到它，通过 onboarding 描述自己想要的伙伴（名字、性格、说话风格、外貌偏好），系统据此即时生成专属桌面形象；此后伙伴常驻桌面、能主动陪伴、也能调用本机能力帮用户做事。

三个关键词：**定制**（形象与人格由用户定义并生成）、**陪伴**（主动、持续、有记忆）、**伙伴**（交互对象是"他/她/它"，工具能力只是伙伴"会做的事"，不是产品的主角）。

完整设计意图、伙伴生命周期、通信协议不变量见 [design.md](design.md)。

## 模块架构

```
┌──────────────────────────┐
│  Backend (云端大脑)        │
│FastAPI + PostgreSQL + JWT│
│  • 伙伴人格(角色定义+记忆) │
│  • 专属形象资产生成与下发  │
│  • LLM 流式编排 + 提示词   │
│  • 云端工具(搜索/TTS/生图/视频) │
│  • Cron 主动陪伴调度       │
│  Docker / Linux 容器      │
└────────────┬─────────────┘
             │  REST + WebSocket (JSON-RPC 2.0)
             │  唯一通道: WS /api/chat/ws?token=<jwt>
             ▼
┌──────────────────────────┐
│  Desktop (伙伴载体+枢纽)  │
│  Electron 42 + React 19  │
│  • 桌面精灵形象渲染(待建) │
│  • 陪伴交互 + onboarding  │
│  • JWT 加密落盘           │
│  • Runner 编排 + WS 中转  │
│  • 统一自更新             │
│  Windows/macOS/Linux 原生 │
└────────────┬─────────────┘
             │  本地 WebSocket (127.0.0.1:0 动态端口)
             │  JSON-RPC 2.0 + 反向 RPC (request_llm)
             ▼
┌──────────────────────────┐
│  Runner (本地手脚)        │
│  Python 3.13 (uv wheel)  │
│  • 终端 / 文件 / 浏览器   │
│  • 代码执行沙箱           │
│  • MCP 动态工具 + Skills  │
│  Windows/macOS/Linux 原生 │
└──────────────────────────┘
```

**职责一句话**：
- **Backend = 大脑**：持久化伙伴角色定义与形象资产、编排对话、装配提示词、调度云端工具与 Cron 主动陪伴
- **Desktop = 伙伴载体 + 枢纽**：渲染桌面精灵形象、承载陪伴交互；持有用户凭证、中转工具调用、管理 Runner 生命周期与自更新
- **Runner = 手脚**：零凭证执行本地工具，需要 LLM 时借 Desktop 代为调用

## 快速开始

### 后端 (Backend)

```bash
cd backend && docker compose up  # 监听 :8000，PostgreSQL 数据库；默认管理员 deskagent/deskagent@admin123（生产前修改）
```

### 桌面 (Desktop，开发模式)

```bash
cd desktop && pnpm install && pnpm dev  # Vite + Electron
# Backend URL 经 desktop/config.json（默认 127.0.0.1，假设后端也在本机）或 electron/config.cjs 配置
```

### Runner

由 installer 安装到 `$DESKAGENT_HOME/runner/.venv`，desktop 启动时自动 spawn。dev 模式手动启动见 [runner/README.md](runner/README.md)。

### 安装包 (Installer)

```bash
# Windows
pwsh scripts/build_client.ps1

# macOS / Linux
bash scripts/build_client.sh
```

产物：
- `release/DeskAgent-Setup-{ver}.{exe|dmg|AppImage}` —— 首次安装 / 卸载 / repair
- `release/DeskAgent-{ver}-update.zip` —— 运行期自更新 payload（走 `electron-updater` 通道）

发布运维：登录 `https://<your-backend>/admin/` → "版本管理" → 选 `release/DeskAgent-{ver}-update.zip` → 上传并发布。

## 文档导航

根 README 只列"是什么 / 怎么跑 / 看哪里"；架构设计、模块行为契约、跨切面安全/错误契约参阅：

| 想了解什么 | 看哪里 |
|-----------|--------|
| 项目总览 / 架构机制 / 通信协议与不变量 / 伙伴生命周期 | [design.md](design.md) |
| Backend 模块结构与实现 | [backend/README.md](backend/README.md) |
| Runner 模块结构与实现 | [runner/README.md](runner/README.md) |
| Desktop 模块结构与实现 | [desktop/README.md](desktop/README.md) |
| Installer 模块结构与协议 | [installer/README.md](installer/README.md) |
| 构建 / 测试 / 发布脚本 | [scripts/README.md](scripts/README.md) |
| 仓库级 AI 协作规范 | [CLAUDE.md](CLAUDE.md) |

## 平台支持

| 模块 | 部署目标 | 兼容性要求 |
|------|---------|-----------|
| Backend | Linux (Docker 容器) | 仅 Linux，无 Windows 兼容要求 |
| Runner | Windows / macOS / Linux 原生 | Windows 是已知风险面（见 [runner/README.md §已知限制](runner/README.md)） |
| Desktop | Windows / macOS / Linux 原生 | Windows 兼容性是已知风险面（见 [desktop/README.md §已知限制](desktop/README.md)） |
| Installer | Windows / macOS / Linux 原生 | Tauri 2 cross-platform；当前 install 协议 v2（含 install-python stage） |

## 信任与安全

跨模块安全契约详见 [design.md §8](design.md)；核心要点：

- **Runner 零凭证**：不持有 Backend token；需借 LLM 时通过反向 RPC 经 Desktop 代调 `POST /api/llm/completion`
- **JWT 加密落盘**：Electron `safeStorage` 跨平台统一（DPAPI / Keychain / libsecret）
- **自更新签名**：Electron 二进制走 `electron-updater` RSA；Python runner wheel 走 `scripts/secrets/update.pub` RSA + SHA-512 双重校验
- **API key fingerprinting**：`GET /api/user/model-config` 只返回 `sk-…XX` 形式的 fingerprint，原始 key 永不离开 Backend
- **Skills 走单独通道**：由 installer 在首装 seed，desktop 自更新不下载

## 开发约定

- 改实现方案 / 架构 / 导出 → **同提交**同步该模块及所有受影响 `README.md`（完整规范见 [CLAUDE.md](CLAUDE.md)）
- 文档用中文，描述当前状态；不写"X 不再 Y / 现在改为 Z"等变化叙事——git log / blame 负责历史
- 提交前：`pwsh scripts/build_client.ps1` 全链路
