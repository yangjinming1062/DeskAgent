# DeskAgent

> 可定制的陪伴型桌面伙伴——你描述想要的伙伴，它生成专属形象，常驻你的桌面主动陪伴、记住你、并帮你做事。

DeskAgent 是一个**根据用户描述定制的、具有专属形象的陪伴型桌面伙伴**。首次安装时你以一颗"蛋"的形态见到它，通过一段对话式 onboarding 描述你想要的伙伴（名字、物种、性格、说话风格、外貌偏好），系统据此即时生成专属桌面形象；此后伙伴常驻桌面、能主动陪伴、也能调用本机能力帮你做事。

三个关键词决定一切产品与技术取舍：**定制**（形象与人格由你定义并生成）、**陪伴**（主动、持续、有记忆的关系，而非一次性问答）、**伙伴**（交互对象是"他/她/它"，工具能力只是伙伴"会做的事"，不是产品的主角）。

## 核心功能

- **定制专属形象**：对话式 onboarding 描述你想要的伙伴，系统即时生成专属形象——从半身头像、全身立绘到 3D 模型与换装，所见即"你的伙伴"。
- **常驻桌面陪伴**：伙伴以透明置顶窗口常驻桌面，3D 实时渲染、时刻"活着"（呼吸、微动作、情绪表情）；可缩放、可拖拽、可扒在屏幕边缘或窗口旁。
- **主动陪伴与长期记忆**：伙伴不被动等你说——它会主动问候、定时提醒、情境化闲聊；随互动累积对你的了解，越处越懂你。
- **语音交互**：文字对话与语音通话双模式，云端/本地语音合成与转写，可选专属音色。
- **替你操作本机**：伙伴能调用终端、文件、浏览器等本机能力帮你做事，叙事上始终是"伙伴在帮忙"，原始技术过程不对你暴露。
- **换装与外观定制**：换装零模型重生——改色、材质、图案秒级热替，几何服装/挂件分钟级装配；模型只在你主动要求时重生。
- **隐私与安全**：云端只管人格与形象资产，不碰你的本机；本机操作由零凭证的本地执行器隔离执行，你的凭证加密落盘。

## 快速开始

### 后端 (Backend)

```bash
cd backend && docker compose up  # 监听 :10620，PostgreSQL 数据库；默认管理员 deskagent/deskagent@admin123（生产前修改）
```

### 桌面客户端 (Client，开发模式)

```bash
cd client && pnpm install && pnpm dev  # Vite + Electron
# Backend URL 在首次登录输入激活码后解包并持久化存入 $DESKAGENT_HOME/desktop-config.json
```

### Runner

由 installer 安装到 `$DESKAGENT_HOME/runner/.venv`，client 启动时自动 spawn。dev 模式手动启动见 [runner/README.md](runner/README.md)。

### 安装包 (Installer)

```bash
# Windows
pwsh scripts/build_client.ps1

# macOS
bash scripts/build_client.sh
```

产物：

- `release/DeskAgent-Setup-{ver}.{exe|dmg}` —— 首次安装 / 卸载 / repair
- `release/DeskAgent-{ver}-update.zip` —— 运行期自更新 payload（走 `electron-updater` 通道）

发布运维：登录 `https://<your-backend>/admin/` → "版本管理" → 选 `release/DeskAgent-{ver}-update.zip` → 上传并发布。

## 文档导航

| 想了解什么 | 看哪里 |
|-----------|--------|
| 项目架构 / 模块边界 / 跨模块不变量 | [ARCHITECTURE.md](ARCHITECTURE.md) |
| 伙伴层产品设计（形象、动画、生命周期、onboarding、陪伴范式） | [DESIGN.md](DESIGN.md) |
| 跨模块协议契约（JSON-RPC 方法 / 枚举 / 事件 / 安全 / 凭据） | [PROTOCOL.md](PROTOCOL.md) |
| Backend 模块结构与实现 | [backend/README.md](backend/README.md) |
| Runner 模块结构与实现 | [runner/README.md](runner/README.md) |
| Client 模块结构与实现 | [client/README.md](client/README.md) |
| Installer 模块结构与协议 | [installer/README.md](installer/README.md) |
| 3D 模型与动画规格（骨骼 / clip / morph） | [docs/MODEL_SPEC.md](docs/MODEL_SPEC.md) |
| Tripo3D 骨骼命名权威参考 | [docs/tripo-spec.md](docs/tripo-spec.md) + [docs/mixamo-spec.md](docs/mixamo-spec.md) |
| 构建 / 测试 / 发布脚本 | [scripts/README.md](scripts/README.md) |
| 仓库级协作 / 文档 / Commit 规范 | [RULES.md](RULES.md) |

> 架构机制、安全契约、自更新与平台已知限制的权威定义见 [ARCHITECTURE.md](ARCHITECTURE.md) 与 [PROTOCOL.md](PROTOCOL.md)，根 README 不重复。
