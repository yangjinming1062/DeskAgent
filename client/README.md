# Client

桌面伙伴形象载体 + 本地枢纽——单 Electron 应用，承担双职责：底层是可信枢纽（凭证、中转、Runner 编排、自更新），上层是桌面伙伴形象与陪伴交互。两层共享同一个 Electron 主进程，但职责严格分离。

## 1. 职责与边界

**职责**：
- 桌面精灵 3D 实时渲染（Three.js + 透明置顶窗口）+ 陪伴式交互 UI（chat / voice call / onboarding / settings）
- 登录鉴权与用户凭证加密落盘（safeStorage）
- 本地 WebSocket 服务端与 Runner 进程生命周期管理
- 双向工具调用路由与反向 RPC 代理中转
- 统一自更新（Electron 二进制 + Runner wheel，两阶段契约）
- **disturbance_tier 的唯一权威**：持有用户偏好 + 活动上下文，独立计算 effective 值并单向推 Backend

**不**做：
- **不渲染伙伴人格层**（角色定义 / 长期记忆 / 主动消息生成）——这是 Backend 责任
- **不执行本机工具**——通过 IPC 委托给 Runner
- **不持有 LLM API 凭证**——所有 LLM 调用经 Backend（即使是反向 RPC 也是经 Client → Backend → LLM）
- **不解析协议 schema**（affect / morph target 等）——Backend 出枚举，Client 按枚举查找，不擅自扩展

架构层定位见 [ARCHITECTURE.md §1 / §2](../ARCHITECTURE.md)；跨模块契约见 [PROTOCOL.md](../PROTOCOL.md)；产品设计见 [DESIGN.md](../DESIGN.md)。

## 2. 设计意图

- **伙伴层与枢纽层共享主进程，职责严格分离**：底层处理协议与安全（凭证、中转、Runner 编排、自更新——这部分是 backend/runner 复用所依赖的不变契约），上层处理形象渲染与用户体验。两层共享同一个 Electron 主进程，但伙伴层不直接接触凭证或 Runner 句柄——一切经枢纽层 IPC。
- **3D 实时渲染 + 程序化兜底**：3D 引擎始终在渲染——GLB 加载成功时骨骼动画 + morph 表情覆盖全部状态；GLB 加载失败时渲染程序化兜底角色（基本体 + 正弦驱动呼吸/眨眼/说话浮动），保证形象从启动第一帧起就"活着"。
- **多骨骼动画库 + 性格标签驱动**：biped 109 clip，其余 6 大 rig（quadruped / avian / serpentine / aquatic / hexapod / octopod）各 20–45+ clip；客户端按 `rig_type` 注入对应动画库，按伴侣性格标签交集匹配驱动动作调度。详见 [docs/MODEL_SPEC.md §2](../docs/MODEL_SPEC.md)。
- **disturbance_tier 唯一权威**：Client 持有用户偏好（`localStorage` 持久化）+ 活动上下文（活动感知器），独立计算 effective 值（应用「手动 quiet 永远不被覆盖」+ immersive/fullscreen → quiet 规则），通过 `companion.set_disturbance_tier` 单向推 Backend；Backend `_disturbance` 字典是镜像，不是独立推导。
- **safeStorage 跨平台统一**：Windows DPAPI / macOS Keychain / Linux libsecret；Renderer 与 Preload **不可**访问 safeStorage 接口，阻断 XSS 窃取凭证。
- **透明置顶精灵窗口作为唯一常驻主窗口**：登录、应用设置、登录态界面是从托盘唤起的按需工具窗口，不常驻——"对话发生在角色身边"。Windows close = hide to tray；macOS close = hide window 但保留 Dock 图标。
- **自更新两阶段契约**（Stage 1 prefetch + Stage 2 install）：保证升级中途断网或崩溃不会变砖。Stage 1 在 OLD Electron 里下载并强校验签名 + SHA-512；Stage 2 在 NEW Electron 里原地升级 wheel 与 `server.py`，venv 永不被改名或移动。

## 3. 架构地图

```
client/
├── main/                  # CommonJS *.cjs — Electron 主进程
│   ├── entry.cjs          # 启动入口 + 自更新挂载
│   ├── ipc/               # IPC handler 命名空间（auth / media / connection / runner）
│   ├── runner/            # Runner 进程编排（bridge + reverse-rpc + updater）
│   ├── security/          # 路径白名单 + 凭证保护
│   └── lifecycle/         # tray + 单实例锁 + 关闭拦截
├── renderer/              # ESM *.{ts,tsx} — Vite 编译
│   ├── shared/            # 跨窗口共享层
│   ├── companion/         # 伙伴层（精灵窗口 + 3D + onboarding + chat UI）
│   │   └── 3d/            # Three.js 引擎 + 动画 clip 库 + MorphController
│   ├── hub/               # 枢纽层（托盘唤起的工具窗口）
│   ├── app.tsx            # 角色分发点
│   └── main.tsx
├── scripts/               # 构建/测试钩子（不进 package.json#scripts）
└── assets/                # icon
```

**双 runtime 不可混用**：`main/*.cjs`（CommonJS，不经 vite/tsc）+ `renderer/**/*.{ts,tsx}`（ESM, Vite 编译）。绝不能交叉用 `.cjs` 写 renderer 或 `.ts` 写 main。

**renderer 内部跨模块边界**：`companion` ↔ `hub` 是**两个窗口**而非一个工程的两个层——它们的代码历史上不该相互依赖：

| 起点 → 终点 | 许可 |
|---|---|
| `companion` → `shared` | ✓ |
| `hub` → `shared` | ✓ |
| `companion` ↔ `hub` | ✗（任何 `from '@/hub/...'` 出现在 companion 文件都会被 ESLint 拒绝） |
| `shared` → 任何 | ✗ |

ESLint `no-restricted-imports` 在 `renderer/companion/**` 与 `renderer/hub/**` 各设一道拦截。**唯一例外**是 `renderer/app.tsx`——这是角色分发点，需要同时 import 两个 root。

模块公共面经 `index.ts` barrel 暴露（`@/companion`、`@/hub`、`@/shared`、`@/shared/components/ui`）。模块内部细节不出 barrel。

## 4. 关键设计决策

- **`safeStorage` 跨平台统一封装**：Windows DPAPI / macOS Keychain / Linux libsecret；所有平台走同一组 API（`safeStorage.encryptString` / `decryptString`）。Renderer 与 Preload 进程**不可**访问 safeStorage 接口——阻断 XSS 窃取凭证。**为什么不自己写加密**：OS 原生机制与用户登录态绑定，重启/换用户自动失效；自实现加密无法保证密钥生命周期。
- **精灵窗口透明需要双重保证**：`BrowserWindow.transparent: true` **加** 渲染层 `body` 透明（`html[data-role='sprite'] body { background: transparent }`，`data-role` 由 `index.html` 内嵌脚本在 `<head>` 解析时同步设置）。两者缺一，body 背景色会在桌面剩余区域盖满屏幕——违背"伙伴应不干扰用户正常工作"的契约。
- **交互范围仅限可见矩形**（透明窗口交互陷阱）：Electron `setIgnoreMouseEvents` 是窗口级二元开关——要么全捕获要么全穿透。要在屏幕尺寸的透明窗口里只让"看得见"的区域捕获，其余继续穿透给桌面其他应用，所有 overlay 把自己的面板 bbox 注册到 `companion/interactive-regions.ts`，由 `SpriteStage` 的全局 `mousemove` 唯一做命中测试再切换 `setIgnoreMouseEvents`。任何 overlay 都不能再用 `setIgnoreMouseEvents({ignore:false})` 一刀切捕获整个窗口——那会立刻把桌面的其他应用"锁死"。
- **install → desktop auth bootstrap one-shot**：`$DESKAGENT_HOME/agent-session-bootstrap.json`（schema_version=1）由 installer 登录成功后写入；Desktop 主进程在 `restoreSession()` 后通过 `consumeBootstrapSession` 消费：原子重命名为 `.consumed` → POST `${baseUrl}/api/user/refresh` 校验 token → `BackendSession::adoptSession` 走 safeStorage 落盘到 `agent-session.json`。任何失败静默删文件回退到未登录态。**为什么不直接 adoptSession**：refresh 校验失败 / 网络断的情况下不能让用户处于"看起来登录但实际失效"的状态。
- **`auth.bootstrap` 持久 baseUrl 而非 token**：`desktop-config.json` 只持久 `baseUrl`，不持久 token；登出只清 `agent-session.json`。登录页每次预填上次的 baseUrl，token 永远不被还原（即便 installer 写入过）。**为什么不持久 token**：token 一旦持久就要承担泄露 + 过期管理成本；refresh-then-adopt 模式更安全。
- **自更新两阶段而非单阶段**：单阶段"下载后直接覆盖"在网络断 / 进程被杀时变砖；两阶段拆分让 Stage 1（旧 Electron 跑）只需下载+校验，Stage 2（新 Electron 跑）才做 file ops，失败回滚到旧版。**为什么不直接 atomic-rename**：atomic-rename 之前需要先完整下载到 staging，与两阶段本质等价，但语义上分阶段更易追踪 Sentinel 与降级。
- **`runner venv 永不被改名或移动`**：升级只改 wheel（`pip install --upgrade`），venv 路径稳定；任意升级阶段崩溃时旧版 Runner 依赖树仍完全可用。
- **STT 默认本地优先 / TTS 默认云端优先**（见 [DESIGN.md §7](../DESIGN.md)）：本地零成本，云端音色音质优；Engine 路由由 `main/ipc/media.cjs` 在 IPC 边界读 short-TTL 缓存决策（`auto` / `local` / `cloud` 三档），不暴露在 Desktop 设置面板——运维/部署侧决策。
- **`voice id` 不跨引擎**：云端 voice id（provider 目录中的 id）与本地 voice id（Piper `en_US-amy-medium` 格式）属于不同命名空间；`media.cjs` 路由到本地时不传 caller 的 voice。用户在伙伴设置中选的音色仅在云端路径生效。

## 5. 与外部的契约

| 契约 | 方向 | 在哪定义 |
|------|------|---------|
| 伙伴层 JSON-RPC 方法（onboarding / avatar / companion / model / tts） | 对 Backend | [PROTOCOL.md §1.1](../PROTOCOL.md) |
| 事件类型（`companion.affect` / `model.ready` / `wardrobe.updated` 等） | 接 Backend 推送 | [PROTOCOL.md §1.2](../PROTOCOL.md) |
| Affect emotion / locale 枚举消费 | 接 Backend | [PROTOCOL.md §1.3](../PROTOCOL.md) |
| 资产 URL 5 分钟 HMAC 签名消费 + 本地缓存 | 接 Backend | [PROTOCOL.md §1.4](../PROTOCOL.md) |
| 错误信封 `{error, reason, status}` + JSON-RPC 错误码脱敏消费 | 接 Backend | [PROTOCOL.md §1.5](../PROTOCOL.md) |
| Runner `runner_ready` payload + capabilities 消费 | 对 Runner | [PROTOCOL.md §2.3](../PROTOCOL.md) |
| 反向 RPC `request_llm` 代理 | 对 Runner + Backend | [PROTOCOL.md §3](../PROTOCOL.md) |
| 反向 RPC 速率守卫（200 帧 / 1MB 上限） | 对 Runner（Client 转发前限流） | [PROTOCOL.md §3](../PROTOCOL.md) |
| disturbance_tier 权威边界 | 对 Backend（Client 推、Backend 镜像） | [ARCHITECTURE.md §5](../ARCHITECTURE.md) + [DESIGN.md §6.2](../DESIGN.md) |
| disturbance_tier 双层模型（`$userPreferredTier` + `$effectiveTierOverride` + `$effectiveTier`） | 本模块独有（持久层 + 活动感知器） | 本 README §2 + DESIGN §6.2 |
| safeStorage 跨平台一致（DPAPI/Keychain/libsecret） | 平台 | [PROTOCOL.md §5.3](../PROTOCOL.md) |
| Electron 二进制自更新（`electron-updater` RSA + Runner wheel RSA + SHA-512） | 对 Backend | [PROTOCOL.md §5.6](../PROTOCOL.md) |
| 自更新两阶段契约（Stage 1 prefetch / Stage 2 install + Sentinel + 降级） | 对 Backend | [PROTOCOL.md §5.6](../PROTOCOL.md) + [ARCHITECTURE.md §9](../ARCHITECTURE.md) |
| IPC 命名空间 `deskagent:*` 前缀（`deskagent:sprite:*` / `deskagent:auth:changed` 等） | 本模块独有 | 本 README §3 |
| 动画状态机（`IDLE` / `LISTENING` / `THINKING` / `SPEAKING` / `WORKING` / `EMOTIONAL` / `SLEEPING` / `INTERACTING` / `DISCONNECTED`） | 本模块独有（消费 Backend `affect` + 用户操作） | 本 README §2 + DESIGN §2 |
| 空间行为 locales（`home` / `chat` / `perch` / `target` / `roam` / `sleep`）+ 缩放范围 0.5×–2× | 本模块独有（消费 Backend `affect.locale/target`） | 本 README §2 + DESIGN §3 + PROTOCOL §1.3 |
| Companion personality tag 驱动的动画调度（`selectClipByTags`） | 本模块独有 | 本 README §2 + [docs/MODEL_SPEC.md §2](../docs/MODEL_SPEC.md) |
| auth bootstrap one-shot schema（`schema_version: 1`） | 接 installer | [installer/README.md §5](../installer/README.md) |
| Skills frontmatter 平台过滤（仅 `macos` / `windows`，历史 `linux` 值兼容翻译表） | 本模块独有 | 本 README §3 + [installer/README.md §2](../installer/README.md) |

## 6. 已知限制

| 限制 | 说明 |
|------|------|
| **透明窗口平台差异** | 远程显示（X11/VNC/RDP）无法合成透明层，精灵窗口降级为非透明（`SPRITE_TRANSPARENT`）；macOS / Windows 本地会话支持良好 |
| **GLB / 衣柜贴图不走 `deskagent:api:asset`** | `3d/CharacterController.ts` 的 `GLTFLoader` 与 `TextureLoader`、`settings-overlay.tsx` 的衣柜缩略图仍直接加载 Backend 签名 URL，`public_url_prefix` 不可达时静默失败（GLB 退化为程序化兜底角色，贴图不生效）。data URL 方案不适用于 GLB（数十 MB，base64 再涨 33%） |
| **托盘 Settings 中"重载 MCP"不可用** | gateway 仅在精灵窗口 boot；从托盘打开的 framed 工具窗口无 gateway，`hub/settings/mcp-settings.tsx` 的 reload 按钮优雅报"gateway 不可用"。其余 settings（runnerConfig 等 REST）不受影响 |
| **`voice-call-dock.tsx` useEffect 依赖故意省略 `[gatewayState]`** | 麦克风挂载/take-down 由 `[requestGateway]` 触发；reconnect 重入若再加 `gatewayState` 会再次重新挂麦克风导致当前通话被打断 |
| **Electron 42 + pnpm 11 需 hoisted** | 失去 phantom-deps 防护；等 Electron ESM 主进程支持 |
| **`.cjs` + `.ts` 双 runtime** | 新增 main 模块用 `.cjs`，renderer 用 `.ts/.tsx`；等 Electron ESM 主进程支持 |
| **Windows 单实例锁 dev opt-out** | `DESKAGENT_DESKTOP_DISABLE_SINGLE_INSTANCE_LOCK=1` 强制多实例运行，便于并行调试窗口 |
| **STT/TTS 引擎选择不在 Desktop 设置面板暴露** | 三档（`auto` / `local` / `cloud`）+ `stt.silent_fallback` 走 Backend 配置（`stt.engine` / `tts.engine`），不在 sprite UI 暴露 |