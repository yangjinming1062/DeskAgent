# Client

桌面伙伴形象载体 + 本地枢纽——单 Electron 应用，承担双职责：底层是可信枢纽（凭证、中转、Runner 编排、自更新），上层是桌面伙伴形象与陪伴交互。两层共享同一个 Electron 主进程，但职责严格分离。

## 1. 职责与边界

**职责**：
- 桌面精灵 3D 实时渲染（Three.js + 透明置顶窗口）+ 陪伴式交互 UI（chat / voice call / onboarding / settings）
- 登录鉴权与用户凭证加密落盘（safeStorage）
- 本地 OS IPC 服务端（命名管道 / UDS）与 Runner 进程生命周期管理
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
- **3D 实时渲染 + 三级降级**：GLB 加载成功时骨骼动画 + morph 表情覆盖全部状态；GLB 不可用（生成中/失败/无 key/换模空挡）时静态精灵相册接管——`StaticSprite` 层按状态/情绪向后端相册请求身份一致的透明背景立绘（250ms 淡切 + 3.4s 呼吸），GLB 真正解析完成后淡出交还；相册不可用才渲染程序化兜底蛋（暖琥珀蛋壳 `0xfff4d6` + emissive 3.4s 呼吸光 + 裂痕装饰 + 呼吸/眨眼/说话浮动），保证形象从启动第一帧起就"活着"且永远是用户选定的角色。
- **多骨骼动画库 + 性格标签驱动**：biped 109 clip，其余 6 大 rig（quadruped / avian / serpentine / aquatic / hexapod / octopod）各 20–45+ clip；客户端按 `rig_type` 注入对应动画库，按伴侣性格标签交集匹配驱动动作调度。详见 [docs/MODEL_SPEC.md §2](../docs/MODEL_SPEC.md)。
- **disturbance_tier 唯一权威**：Client 持有用户偏好（`localStorage` 持久化）+ 活动上下文（活动感知器），独立计算 effective 值（应用「手动 quiet 永远不被覆盖」+ immersive/fullscreen → quiet 规则），通过 `companion.set_disturbance_tier` 单向推 Backend；Backend `_disturbance` 字典是镜像，不是独立推导。
- **safeStorage 跨平台统一**：Windows DPAPI / macOS Keychain / Linux libsecret；Renderer 与 Preload **不可**访问 safeStorage 接口，阻断 XSS 窃取凭证。
- **透明置顶精灵窗口作为唯一常驻主窗口**：登录、应用设置、登录态界面是从托盘唤起的按需工具窗口，不常驻——"对话发生在角色身边"。Windows close = hide to tray；macOS close = hide window 但保留 Dock 图标。
- **自更新两阶段契约**（Stage 1 prefetch + Stage 2 install）：保证升级中途断网或崩溃不会变砖。Stage 1 在 OLD Electron 里下载并强校验签名 + SHA-512；Stage 2 在 NEW Electron 里原地升级 wheel 与 `server.py`，venv 永不被改名或移动。
- **网关连续性与去重重放（ACK & Session Resume）**：`JsonRpcGatewayClient` 记录单调递增的连接级 `lastReceivedSeq` 并对网络重叠帧进行幂等去重，同时在后台定期批量向服务端发送 `session.ack`（标准 RPC 请求）。断网重连时携带 `last_seq` 触发增量 Replay，保障网络抖动下正在进行的流式对话和工具调用无感续接；若服务端重启或序列号失同步（返回 `resumed: false` 或降级 `session.get_main`），客户端自动同步重置 `lastReceivedSeq = current_seq` 避免新事件黑洞（普通活连接会话切换不重置）。

## 3. 架构地图

```
client/
├── main/                  # TypeScript *.ts — Electron 主进程（经 tsup 编译打包至 dist-electron/）
│   ├── entry.ts           # 启动入口 + 自更新挂载
│   ├── preload.ts         # Preload 桥接脚本
│   ├── ipc/               # IPC handler 命名空间（auth / media / connection / runner 等）
│   ├── runner/            # Runner 进程编排（bridge + reverse-rpc + updater）
│   ├── security/          # 路径白名单 + 凭证保护 + hardening
│   ├── lifecycle/         # tray + 单实例锁 + 关闭拦截
│   ├── backend/           # REST 客户端 + session 会话管理 + ws 探测
│   └── shared/            # 强类型 IPC 契约定义与通用工具库
├── renderer/              # ESM *.{ts,tsx} — Vite 编译
│   ├── shared/            # 跨窗口共享层
│   ├── companion/         # 伙伴层（精灵窗口 + 3D + onboarding + chat UI）
│   │   └── 3d/            # Three.js 引擎 + 动画 clip 库 + MorphController
│   ├── hub/               # 枢纽层（托盘唤起的工具窗口）
│   ├── app.tsx            # 角色分发点
│   └── main.tsx
├── scripts/               # 构建/测试钩子
└── assets/                # icon
```

**TypeScript 全栈类型安全**：主进程采用 `main/*.ts`（经 `tsup` 统一编译输出 CJS 产物至 `dist-electron/`），渲染进程采用 `renderer/**/*.{ts,tsx}`（Vite 编译）。通过 `main/shared/ipc-contracts.ts` 与 `renderer/shared/types/global.d.ts` 共享严格的 IPC Channel 与 Payload 契约。

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
- **激活码持久 + session JWT 内存 only**：`agent-session.json` 持久化激活码（加密）+ baseUrl + user。`restoreSession()` 读取激活码后调 `/api/user/activate` 获取 session JWT。session JWT 的 proactive refresh 机制（`/api/user/refresh`）保持不变。**为什么不持久 session JWT**：JWT 一旦持久就要承担泄露 + 过期管理成本；激活码 + 每次启动重新激活的模式更安全。
- **自更新两阶段而非单阶段**：单阶段"下载后直接覆盖"在网络断 / 进程被杀时变砖；两阶段拆分让 Stage 1（旧 Electron 跑）只需下载+校验，Stage 2（新 Electron 跑）才做 file ops，失败回滚到旧版。**为什么不直接 atomic-rename**：atomic-rename 之前需要先完整下载到 staging，与两阶段本质等价，但语义上分阶段更易追踪 Sentinel 与降级。
- **`runner venv 永不被改名或移动`**：升级只改 wheel（`pip install --upgrade`），venv 路径稳定；任意升级阶段崩溃时旧版 Runner 依赖树仍完全可用。
- **STT 默认本地优先 / TTS 默认云端优先**（见 [DESIGN.md §7](../DESIGN.md)）：本地零成本，云端音色音质优；Engine 路由由 `main/ipc/media.cjs` 在 IPC 边界读 short-TTL 缓存决策（`auto` / `local` / `cloud` 三档），不暴露在 Desktop 设置面板——运维/部署侧决策。
- **`voice id` 不跨引擎**：云端 voice id（provider 目录中的 id）与本地 voice id（Piper `en_US-amy-medium` 格式）属于不同命名空间；`media.cjs` 路由到本地时不传 caller 的 voice。用户在伙伴设置中选的音色仅在云端路径生效。
- **3D 渲染栈 `WebGPURenderer` + 四层回退**：`Engine.create()` 异步工厂按 WebGPU 后端 → three 内置 WebGL2 后端（同一 API 面/场景图，零代码）→ 经典 `WebGLRenderer` → `EngineInitError`（static-sprite 层兜底，永不空白）逐级降级。经典回退必须换新 canvas——曾成功获取过 webgpu 上下文的 canvas 再也要不到 webgl2 上下文，所以 canvas 由 Engine 自建自管（React 只渲染容器），companion-3d 的 load/outfit effects 一律 await 引擎就绪 Promise 而非早退，避免首模型在异步 boot 窗口被静默丢弃。PMREMGenerator 按渲染器类型分支——经典版深度依赖 WebGLRenderer internals，传入 WebGPURenderer 构造期即抛。**为什么不停留在 WebGLRenderer**：混合显卡笔记本上 `powerPreference:'high-performance'` 会强制唤醒独显（桌宠场景远低于 dGPU 门槛），且 TSL compute 需要节点材质系统才能把布料物理搬进 GPU。
- **渲染循环自研调度与能耗档位控制**：`Engine.ts` 内部通过 `scheduleNext()` 自主调度动画循环，支持 `setPowerProfile`（`active` 60fps / `idle` 30fps / `dormant` 0.5fps 低频 Timer 轮询）以及 `stop()` 彻底终止循环；不依赖 Three.js 内部循环，解决休眠档位能耗控制。
- **3D 模型传输与本地缓存优化**：身体与服装 GLB 均采用 Draco 压缩（体积降低 5–10×），渲染端通过 `createGLTFLoader()`（集成 `DRACOLoader`，解码器由 Vite `assets/draco/` 本地托管）流式解压渲染；主进程按 `content_hash` 在 `$SPIRITAGENT_HOME/cache/models/` 磁盘缓存，支持 HTTP Range 断点续传与 LRU 淘汰。
- **主进程 TypeScript 构建**：主进程源码使用 TypeScript (`main/*.ts`)，由 `tsup` 统一编译打包为 CJS 输出至 `dist-electron/`，与渲染进程共享严谨的静态类型校验。
- **Windows 单实例锁 dev opt-out**：`SPIRITAGENT_DESKTOP_DISABLE_SINGLE_INSTANCE_LOCK=1` 强制多实例运行，便于并行调试窗口。

## 5. 与外部的契约

| 契约 | 方向 | 在哪定义 |
|------|------|---------|
| 伙伴层 JSON-RPC 方法（onboarding / avatar / companion / model / tts） | 对 Backend | [PROTOCOL.md §1.2](../PROTOCOL.md) |
| 事件类型（`companion.affect` / `companion.assets.updated` / `model.ready` / `wardrobe.updated` 等）与聊天流事件（`message.start` / `message.delta` / `message.break` / `message.complete` / `tool.*` / `error`） | 接 Backend 推送 | [PROTOCOL.md §1.3](../PROTOCOL.md) |
| Affect emotion / locale 枚举消费 | 接 Backend | [PROTOCOL.md §1.4](../PROTOCOL.md) |
| 资产 URL 5 分钟 HMAC 签名消费 + 本地缓存 | 接 Backend | [PROTOCOL.md §1.5](../PROTOCOL.md) |
| 错误信封 `{error, reason, status}` + JSON-RPC 错误码脱敏消费 | 接 Backend | [PROTOCOL.md §1.6](../PROTOCOL.md) |
| Runner `runner_ready` payload + capabilities 消费 | 对 Runner | [PROTOCOL.md §2.3](../PROTOCOL.md) |
| 反向 RPC `request_llm` 代理 | 对 Runner + Backend | [PROTOCOL.md §3](../PROTOCOL.md) |
| 反向 RPC 速率守卫（200 帧；文本 1MB / 多模态视觉 10MB 上限） | 对 Runner（Client 转发前限流） | [PROTOCOL.md §3](../PROTOCOL.md) |
| disturbance_tier 权威边界 | 对 Backend（Client 推、Backend 镜像） | [ARCHITECTURE.md §5](../ARCHITECTURE.md) + [DESIGN.md §6.2](../DESIGN.md) |
| disturbance_tier 双层模型（`$userPreferredTier` + `$effectiveTierOverride` + `$effectiveTier`） | 本模块独有（持久层 + 活动感知器） | 本 README §2 + DESIGN §6.2 |
| LLM 反应与自主开关 (`llmReactions` / `llmAffect` / `llmAutonomy`) | 本模块独有（`localStorage` 持久化，不上报后端） | [DESIGN.md §6.3](../DESIGN.md) |
| safeStorage 跨平台一致（DPAPI/Keychain/libsecret） | 平台 | [PROTOCOL.md §5.3](../PROTOCOL.md) |
| Electron 二进制自更新（`electron-updater` RSA + Runner wheel RSA + SHA-512） | 对 Backend | [PROTOCOL.md §5.5](../PROTOCOL.md) |
| 自更新两阶段契约（Stage 1 prefetch / Stage 2 install + Sentinel + 降级） | 对 Backend | [PROTOCOL.md §5.5](../PROTOCOL.md) |
| IPC 命名空间 `spiritagent:*` 前缀（`spiritagent:sprite:*` / `spiritagent:auth:changed` 等） | 本模块独有 | 本 README §3 |
| 动画状态机（`IDLE` / `LISTENING` / `THINKING` / `SPEAKING` / `WORKING` / `EMOTIONAL` / `SLEEPING` / `INTERACTING` / `DISCONNECTED`） | 本模块独有（消费 Backend `affect` + 用户操作） | 本 README §2 + DESIGN §2 |
| 空间行为场所：协议 5 项（`home` / `chat` / `perch` / `roam` / `sleep`）+ 客户端内部 `target`（仪式行走专用，工具调用触发、非协议枚举）+ 缩放范围 0.5×–2× | 本模块独有（消费 Backend `affect.locale`；`target` 仅本模块内部触发） | 本 README §2 + DESIGN §3 + PROTOCOL §1.3 |
| Companion personality tag 驱动的动画调度（`selectClipByTags`） | 本模块独有 | 本 README §2 + [docs/MODEL_SPEC.md §2](../docs/MODEL_SPEC.md) |
| 激活码格式（base64 编码 `{b, t}` JSON） | 对 Backend | [PROTOCOL.md §5.3](../PROTOCOL.md) |
| Skills frontmatter 平台过滤（仅 `macos` / `windows`，历史 `linux` 值兼容翻译表） | 本模块独有 | 本 README §3 + [installer/README.md §2](../installer/README.md) |

## 6. 已知限制

| 限制 | 说明 |
|------|------|
| **透明窗口平台差异** | 远程显示（X11/VNC/RDP）无法合成透明层，精灵窗口降级为非透明（`SPRITE_TRANSPARENT`）；macOS / Windows 本地会话支持良好 |
| **WebGPU 透明合成依赖 premultiplied** | `alpha:true` 时 three 自动以 `alphaMode:'premultiplied'` 配置 canvas；透明精灵窗下若出现黑晕/黑底即走回退链（决策写 dev log） |
| **几何服装与布料碰撞精度** | 服装与身体的碰撞由 CPU 代理网格（`BodyCollider`，~4096 顶点）结合骨骼球计算，在极端曲率处存在数毫米内的近似误差；换装 PBR 支持 5 通道（含 displacement 视差置换）。 |
| **Electron 42 + pnpm 11 需 hoisted** | 失去 phantom-deps 防护；等 Electron ESM 主进程支持 |
| **连发消息 4s 合并窗口（非 BUG）** | 用户快速连发多条时，`chat-store.ts` 用 4s 防抖窗口（`FLUSH_DEBOUNCE_MS`）把消息合并成一次 `prompt.submit` batch，只触发一次 LLM 调用（[DESIGN.md §6.6](../DESIGN.md)）。这是**刻意**的合并，不是发送延迟：窗口内逐条重置计时器，且 `message.complete` / `error` / 用户停止时会立即 flush。 |
