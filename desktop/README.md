# Desktop

桌面伙伴形象载体 + 本地枢纽——Electron 客户端。上层渲染常驻桌面的伙伴形象并承载陪伴式交互，下层连接云端 Backend 与本地 Runner，持有用户凭证、中转工具调用、管理 Runner 生命周期与自更新。

设计文档：[design.md](../design.md) §2 / §3 / §5 / §7 / §10

## 双层定位

DeskAgent 重新定位为陪伴型桌面伙伴后，Desktop 是**双层叠加**的单 Electron 应用：

| 层 | 职责 | 状态 |
|----|------|------|
| **伙伴层**（上层） | 桌面精灵形象渲染、onboarding（蛋→角色定义→孵化）、陪伴式交互 UI | **全新构建** |
| **枢纽层**（下层） | 凭证加密落盘、WS 中转、Runner 进程编排、反向 RPC 代理、两阶段自更新、本地文件系统拦截 | **保留复用**（backend/runner 复用所依赖的不变契约） |

两层共享同一个 Electron 主进程，职责严格分离：枢纽层处理协议与安全，伙伴层处理形象渲染与用户体验。伙伴层不直接接触凭证或 Runner 句柄，一切经枢纽层 IPC。

## 当前架构

```
desktop/
├── assets/                      # icon.{png,ico,icns}（三平台共用）
├── electron/                    # ★ 枢纽层（主进程，CommonJS，Electron 42 不支持 ESM）——整体保留
│   ├── main.cjs                 # lifecycle + 顶层共享 state
│   ├── preload.cjs              # contextBridge → window.zastDesktop.*
│   ├── config.cjs               # backend URL 加载器（app.isPackaged 分流 + 缺文件 fallback）
│   ├── utils.cjs                # 跨 ipc 模块共用纯函数
│   ├── mime.cjs                 # ext → MIME 映射表
│   ├── client-context.cjs       # buildClientContext（environment/platform/skills）
│   ├── paths.cjs                # $ZAST_HOME / userData / venv 等路径常量
│   ├── venv-python.cjs          # runner venv python 解析 + 冒烟
│   ├── runner-updater.cjs       # 两阶段 runner 更新（prefetch → install）
│   ├── lib/                     # 跨 ipc 共享 helper
│   │   ├── config-writer.cjs    # atomic write + YAML 校验 + in-flight 锁（runner-config + skills 共享）
│   │   ├── skill-index.cjs      # skills category 索引 + enabled 状态
│   │   └── toolset-index.cjs    # toolset category 索引 + enabled 状态
│   ├── ipc/                     # IPC 命名空间，每个 export registerXxxIpc(deps)——见 §IPC 命名空间
│   ├── backend-session.cjs      # 登录 / JWT safeStorage 加密 / 会话恢复 / changePassword / getModelConfig（5min cache）
│   ├── backend-client.cjs       # Backend REST 客户端
│   ├── gateway-ws-probe.cjs     # Backend WS 联通性探测
│   ├── runner-process.cjs       # Runner 子进程管理
│   ├── runner-rpc-ws.cjs        # 本地 WS server（127.0.0.1:0）+ JSON-RPC 2.0
│   ├── runner-reverse-rpc.cjs   # 反向 RPC 代理（request_llm → /api/llm/completion）
│   ├── runner-bridge.cjs        # 编排：process + ws-server + reverse-rpc
│   ├── hardening.cjs            # safeStorage / 路径白名单 / timeout 解析
│   ├── bootstrap-platform.cjs   # WSL / X11 / RDP 探测
│   ├── tray.cjs                 # 系统托盘 + 关闭按钮拦截 + 单实例锁转发 + close-to-tray 守卫
│   └── entitlements.mac*.plist  # macOS 签名
├── src/                         # 渲染进程（TypeScript + React）
│   ├── main.tsx                 # 入口
│   ├── global.d.ts              # window.zastDesktop 类型声明
│   ├── zast/                    # ★ REST helpers + ZastGateway class（WS 连接）——保留（伙伴也走此通道）
│   ├── lib/gateway-protocol/    # ★ JsonRpcGatewayClient——保留（WS 协议客户端）
│   ├── store/                   # ★ nanostores atoms——auth/gateway 基础 store 保留；session store 改造适配伙伴交互
│   ├── app/
│   │   ├── login-gate.tsx       # ★ 认证门控——保留
│   │   ├── desktop-controller.tsx  # ✗ 登录后主控制器——重做（伙伴交互编排）
│   │   ├── gateway/hooks/       # ★ WS 连接 hooks（use-gateway-boot / use-gateway-request）——保留
│   │   ├── login/               # ◐ 登录页——保留逻辑，调整视觉
│   │   ├── chat/                # ✗ 聊天主体（composer / sidebar / right-rail / thread）——重做为伙伴交互
│   │   ├── session/hooks/       # ◐ 消息流处理（use-message-stream / use-prompt-actions）——逻辑保留，UI 消费方式改
│   │   ├── right-sidebar/       # ✗ 文件树 + 终端面板——评估移入"开发者面板"或移除
│   │   ├── artifacts/           # ✗ 产物预览——评估
│   │   ├── insights/            # ✗ 用量仪表板——评估（可选保留）
│   │   ├── settings/            # ◐ 设置页——保留，围绕伙伴重组
│   │   ├── command-palette/     # ✗ 命令面板——评估
│   │   └── overlays/            # ✗ overlay 组件——评估
│   ├── components/              # ◐ 跨 feature 复用 UI——按需裁剪
│   ├── hooks/                   # ◐ 通用 hooks——按需裁剪
│   ├── i18n/                    # ★ 翻译（en / zh,默认 zh）——保留
│   ├── themes/                  # ★ 6 主题 + CSS variables——保留
│   ├── types/                   # ◐ 共享类型——按需调整
│   └── styles.css               # ★ Tailwind v4 + 自定义 token——保留
├── scripts/                     # 构建钩子
├── config.json                  # 后端 URL 默认值
└── package.json                 # Electron 42 / React 19 / pnpm 11
```

图例：★ 保留 · ◐ 改造 · ✗ 重做/评估

### 关键架构决策（保留层，不变）

- **双 runtime**：`electron/*.cjs`（CommonJS，不经 vite/tsc）+ `src/**/*.ts`（ESM，Vite 编译）。绝不混用——main 进程用 `.cjs`，renderer 用 `.ts/.tsx`。
- **单 chunk 构建**：Vite 产出单 JS bundle（避免 electron-builder OOM 扫描千级 chunk）。
- **hoisted nodeLinker**：Electron 42 + pnpm 11 兼容性要求。
- **关闭按钮语义**：Win/Linux/WSL 上 close 按钮 = hide to tray；macOS 上 close 按钮 = hide window，Dock icon 保留。统一由 `electron/tray.cjs::installCloseInterceptor` 实现。
- **单实例锁**：`app.requestSingleInstanceLock()` 在 `app.whenReady()` 之前调用；`second-instance` 事件唤起现有窗口。dev opt-out 环境变量 `ZAST_DESKTOP_DISABLE_SINGLE_INSTANCE_LOCK=1`。
- **Windows AppUserModelID**：`'io.zast.agent'`（与 `package.json#build.appId` 对齐），Windows 通知分组依赖此 ID。

## 伙伴层设计（全新，待实现）

### 窗口架构：精灵窗口 + 面板窗口

Desktop 采用双窗口架构承载伙伴体验：

- **精灵窗口**（新增）：透明背景、置顶、无边框的桌面常驻窗口，渲染伙伴形象与轻量交互（对话气泡、情绪动画、唤起提示）。点击穿透可控——空闲时仅形象可交互，对话时显示气泡。技术形态参考现有 recorder 透明工具栏窗口的透明/置顶经验。
- **面板窗口**（改造自现有主窗口）：用户唤起后展开的完整界面，承载对话流、设置、工具结果查看等"重交互"。复用现有 `src/zast`、`src/lib/gateway-protocol`、WS 连接 hooks 与 session 消息流逻辑，但 UI 围绕伙伴重组。

形象资产由 Backend 生成并下发（[design.md §7](../design.md)），Desktop 拉取后本地缓存、在精灵窗口渲染。渲染技术（sprite / 序列帧 / Live2D / 骨骼动画）是实现决策，不在此锁定。

### 伙伴生命周期落点

Desktop 是 [design.md §2 伙伴生命周期](../design.md) 在客户端侧的实现载体：

| 阶段 | Desktop 行为 | 窗口 |
|------|-------------|------|
| **蛋** | 首装/首次登录后、角色定义完成前，精灵窗口渲染默认"蛋"形象常驻桌面 | 仅精灵窗口 |
| **角色定义** | onboarding 引导（对话式）收集用户对伙伴的描述，经新 JSON-RPC 方法提交 Backend | 精灵窗口 + onboarding 面板 |
| **形象生成** | 等 Backend 生成形象资产，期间精灵窗口显示"孵化中"过渡态 | 仅精灵窗口 |
| **孵化** | 收到形象资产下发事件，精灵窗口将"蛋"替换为专属形象（仪式感过渡） | 仅精灵窗口 |
| **持续陪伴** | 伙伴常驻桌面，支持用户随时唤起对话与伙伴主动发起交互（Cron/send_message 事件经 WS 下发） | 精灵窗口 + 按需面板 |

### 主动陪伴的客户端落点

Backend 的 Cron / `send_message` 经 WS 事件下发（[design.md §6](../design.md)）后，Desktop 在精灵窗口以符合伙伴人格的方式表达——伙伴形象做出反应 + 对话气泡展示消息，而非系统通知。现有 `use-message-stream.ts` 的事件分发逻辑是改造基础。

## 通信模型（保留层，不变）

### Renderer ↔ Main（IPC）

renderer 通过 `window.zastDesktop.*` 调 main，后者通过 `webContents.send(...)` 推事件。`preload.cjs` 是白名单。chat WS **不走** preload 命名空间，直接由 renderer 的 `ZastGateway` 连 Backend。

### Renderer ↔ Backend（REST + WS）

- **REST**：renderer 经 `window.zastDesktop.api({ path, method, body })` 调 main → `connection.cjs` 转发到 Backend（携带 JWT）。401 时发 `zast:auth:session-expired`。
- **WS**：`ZastGateway extends JsonRpcGatewayClient`，在 renderer 内直接连接 `ws://<backend>/api/chat/ws?token=<jwt>`。main 进程只通过 `zast:gateway:ws-url` 把 URL 推给 renderer。

### Main ↔ Runner（本地 WS）

`runner-bridge.cjs` 编排：
1. `start()` → 启动 WS server（127.0.0.1:0）→ 启动 Runner process（`--desktop-ws`）
2. Runner 连入 → `runner_ready` → bridge 调用 `get_tools` RPC
3. `get_tools` 结果缓存 → emit `running` 事件
4. Renderer 通过 `zast:runner:get-tools` IPC 拉到缓存的 Schema 后，调 `gateway.request('tools.sync', {tools})` 上报给 Backend。

### 反向 RPC（Runner → Backend）

`runner-reverse-rpc.cjs`：处理 Runner 的 `request_llm` → 调 Backend `POST /api/llm/completion` → 透传响应体。守卫：max 200 messages、max 1MB payload。

## IPC 命名空间（保留层）

| 模块 | 通道 | 说明 |
|------|------|------|
| `ipc/auth.cjs` | `zast:auth:login/logout/session/change-password` + `zast:model-config:get` | 登录/登出/会话恢复/修改密码 |
| `ipc/connection.cjs` | `zast:connection`、`zast:gateway:ws-url`、`zast:boot-progress:get`、`zast:api` | 核心 REST 代理 |
| `ipc/runner.cjs` | `zast:runner:status/start/stop/restart/invoke/dispatch` | Runner 生命周期 + 工具调用 + 事件转发 |
| `ipc/terminal.cjs` | `zast:terminal:*` | node-pty 终端（面板窗口开发者模式复用） |
| `ipc/files.cjs` / `ipc/fs.cjs` | 文件读取 / 目录 / git 分支 / @-路径补全 | 本地文件系统操作 |
| `ipc/clipboard.cjs` / `ipc/images.cjs` | 剪贴板 / 图片保存 | 媒体辅助 |
| `ipc/runner-config.cjs` | `zast:runner-config:read/write` | 读写 `$ZAST_HOME/config.yaml`（写后 restartRunnerBridge） |
| `ipc/skills.cjs` | `zast:skills:list` / `zast:skill:set-enabled` / toolset 同类 | skills/toolsets 启停 |
| `ipc/settings.cjs` / `ipc/system.cjs` / `ipc/titlebar.cjs` / `ipc/external.cjs` | 设置 / 麦克风 / 通知 / 版本 / 标题栏 / 外部链接 | 系统集成 |
| `ipc/recorder.cjs` | `zast:recorder:*` | 屏幕录制（透明工具栏窗口——精灵窗口透明技术的现有参照） |
| `ipc/media.cjs` | `zast:media:stt` / `zast:media:tts` | 后端 media 代理（STT 上传 / TTS 流下载）——伙伴语音的基础 |
| `ipc/update.cjs` | `zast:update:*` + 出站 `zast:update-event` | Electron 二进制自更新 |

每个模块 export `registerXxxIpc(deps)`，deps 由 `main.cjs` 显式注入。

### 本地 JSON-RPC 拦截（filesystem-bound calls）

`use-gateway-request.ts::tryLocalIntercept` 在 `requestGateway` 入口处拦截依赖本地文件系统的方法——后端在 Docker 中无法访问：

| 方法 | 拦截逻辑 | IPC |
|------|----------|-----|
| `config.get({key:"project", cwd})` | 读 `.git/HEAD` 解析分支 | `zast:fs:gitBranch` |
| `complete.path({word, cwd})` | 解析 `@` 后路径前缀，列目录项 | `zast:fs:completePath` |

### ClientContext 数据流

Desktop login 时 main 进程把客户端 OS / skills 信息打包发给 Backend（`electron/client-context.cjs::buildClientContext`，仅 main 进程调用），经 `ipc/auth.cjs` 的 `zast:auth:login` 注入、`backend-session.cjs::login()` 传输。Backend `core/system_prompt.py` 据此装配环境感知的系统提示词。不传或字段为 None 等于静默退化到 webui 平台 + 无 skills。

## Electron 二进制自更新（保留层，不变）

Desktop 是预构建产物，走 `electron-updater` 从 Backend `/api/update` 拉取预构建安装包并原子替换。**一次更新同时刷新 desktop 二进制 + Python runner**（wheel + `server.py`），保证两端不会因版本错配而 broken。外层 Tauri `Zast-Setup` 负责 install / uninstall / repair。

两阶段契约（network 在前、file ops 在后，避免网络断在重启后变砖）：

- **Phase 1 — prefetch（OLD Electron 里跑）**：`main.cjs::setupAutoUpdater` 在 `app.whenReady` 后挂载，延迟 30s 自检，下载 Electron 二进制；同时在后台从 `/api/update/latest-runner.yml` 下载 wheel + `server.py` 到 `$ZAST_HOME/runner.staging/`，验 RSA 签名 + SHA-512，写 sentinel。渲染端 "Restart now" 按钮只在 `runner-ready` 事件落地后才可点——不让用户重启到破损态。
- **Phase 2 — install（NEW Electron 里跑）**：`app.whenReady` 早期调 `runnerUpdater.installPending()`：读 sentinel → `runnerBridge.stop()` 释放 Python 句柄（Windows EPERM 风险）→ `pip install --upgrade` 原地升级 → 覆盖 `server.py` → 冒烟 `import zast_agent, server` → `runnerBridge.start()`。
- **venv 永不被改名/移动/替换**——只有 venv 内部 wheel 被升级；任意失败在 `finally` 块 `runnerBridge.start()` 拉起旧 runner；`attempt_count >= 3` 删 sentinel 并 emit `runner-failed {recoverable: false}`。

伙伴形象资产与角色定义云端持久化（[design.md §7](../design.md)），自更新只影响本地代码与运行时，不触碰用户的伙伴身份。

签名 keypair：私钥 `scripts/secrets/update.key`、公钥 `scripts/secrets/update.pub`（经 `desktop/package.json#build.extraResources` 复制到 packaged desktop，`runner-updater.cjs` 启动时读取校验 `latest-runner.yml` 的 RSA 签名）。`desktop/package.json#build.publish.url` 现为 `https://resolved-at-runtime.invalid/api/update`——实际 fetch URL 始终来自 `setFeedURL`。

## 安全（保留层，不变）

- **Token 加密存储**：JWT 经 `safeStorage` 加密落盘，userData 目录权限由 OS 控制。
- **Model Config IPC 边界**：`zast:model-config:get` 返回前投影仅 `llm_*` 字段，剥离 GCS 凭据；recorder 通过 `session.getModelConfig()` 读完整对象，共享 5min cache。
- **路径白名单**：`hardening.cjs` 的 `resolveReadableFileForIpc` 拒绝路径遍历、符号链接逃逸、超大读取、敏感文件（.env/.ssh/.pem 等）。
- **Runner 进程隔离**：Runner 作为独立子进程，所有工具调用经本地 WS 中转。

## 行为契约

登录流程 / 工具调用中转 / Runner 自杀机制见 [design.md §5](../design.md)（工具调用完整链路）与 [§8](../design.md)（安全模型）。

`main.cjs` 顶层构造 `bridgeDeps` 对象，承载 `backendSession` + `runnerBridge` 两个跨模块单例。`ipc/auth.cjs` 写 `deps.backendSession`，`ipc/runner.cjs` 写 `deps.runnerBridge`。

## 重构行动项

Desktop 是新定位下改动最大的模块：枢纽层（`electron/`）整体保留，伙伴层（`src/` 渲染层 UI）大幅重做。

### 保留（不动）
- **`electron/` 全部主进程枢纽**：`main.cjs` / `preload.cjs` / `config.cjs` / `paths.cjs` / `backend-session.cjs` / `backend-client.cjs` / `runner-*` 系列 / `hardening.cjs` / `tray.cjs` / `lib/*` / 全部 `ipc/*` 命名空间 / 两阶段自更新。
- **渲染层基础设施**：`src/zast/`（REST + ZastGateway）、`src/lib/gateway-protocol/`、`src/app/gateway/hooks/`（WS 连接）、`src/store/auth.ts` + `gateway.ts`、`src/i18n/`、`src/themes/`、`src/styles.css`。
- **session 消息流逻辑**：`src/app/session/hooks/use-message-stream.ts` 的事件分发骨架（伙伴也要消费 WS 事件流），UI 消费方式改造。

### 新增
- **精灵窗口**：透明置顶、无边框、桌面常驻的 Electron `BrowserWindow`（`transparent: true` / `frame: false` / `alwaysOnTop: true` / 可控点击穿透），渲染伙伴形象 + 轻量交互（对话气泡、情绪）。透明窗口技术参照现有 `ipc/recorder.cjs` 的工具栏窗口经验。
- **形象渲染引擎**：消费 Backend 下发的形象资产，在精灵窗口渲染（sprite/序列帧/Live2D 等待选型）。含本地缓存管理。
- **onboarding 流程**：蛋形态展示 → 对话式角色定义引导 → 提交 Backend → 孵化中过渡态 → 收到形象资产后的孵化过渡动画。经新 JSON-RPC 方法（`persona.create` / `avatar.generate` / `avatar.status`）与 Backend 交互。
- **伙伴交互 UI**：对话气泡、情绪表达、唤起手势；伙伴主动消息（Cron/send_message 事件）在精灵窗口的人格化呈现。
- **新 IPC 命名空间**（如 `zast:avatar:*`）：形象资产本地缓存读写、窗口可见性/位置管理，遵循现有 `registerXxxIpc(deps)` 模式。

### 改造
- **`desktop-controller.tsx`**：从"聊天主控"改为"伙伴交互编排"——精灵窗口生命周期管理 + 面板窗口按需唤起。
- **`src/store/session.ts`**：会话状态适配伙伴交互模型（伙伴是持续的单条关系，而非多会话列表）。
- **设置页 `src/app/settings/`**：围绕伙伴重组（伙伴形象管理、角色定义编辑、语音设置前置），现有 account/mcp/settings 逻辑保留。
- **`preload.cjs`**：新增伙伴层 IPC 通道白名单。

### 删除/评估（确认产品范围后处理）
- **`src/app/chat/composer/`**：富文本编辑器、slash 命令、@补全、代码块等重度"工具型"编辑能力——伙伴对话以自然语言/语音为主，评估是否保留为"开发者模式"或移除。
- **`src/app/right-sidebar/`**：文件树 + 内嵌终端面板——工具型 UI，评估移入开发者面板或移除。
- **`src/app/artifacts/`**、**`src/app/insights/`**、**`src/app/command-palette/`**、**`src/app/overlays/`**：按新定位评估保留/移除。

## 已知限制

| 限制 | 说明 |
|------|------|
| Runner 崩溃后重连窗口有限 | 端点文件 + 重连循环（~5 分钟），超时后 Runner 退出 |
| Electron 42 + pnpm 11 需 hoisted | 失去 phantom-deps 防护；等 Electron ESM 主进程支持 |
| `.cjs` + `.ts` 双 runtime | 新增 main 模块用 `.cjs`，renderer 用 `.ts/.tsx`；等 Electron ESM 主进程支持 |
| 透明窗口平台差异 | Linux 部分桌面环境（无 compositor）透明窗口可能黑底；macOS/Windows 支持良好——精灵窗口需平台降级策略 |
| WSL 下无系统托盘 | Electron Tray API 在 WSL 不可用；降级为 hide-only；精灵窗口在 WSL 下可能受限 |
