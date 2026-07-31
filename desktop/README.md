# Desktop

桌面伙伴形象载体 + 本地枢纽——单 Electron 应用，承担双职责。

设计文档：[ARCHITECTURE.md](../ARCHITECTURE.md) §2 / §3 / §5 / §7 / §10；伙伴层详细交互见 [COMPANION_DESIGN.md](../COMPANION_DESIGN.md)。

## 双层定位

DeskAgent 是**双层叠加**的单 Electron 应用：

| 层 | 职责 | 状态 |
|----|------|------|
| **伙伴层**（上层） | 桌面精灵形象渲染、onboarding（蛋→角色定义→孵化）、陪伴式交互 UI | MVP 已落地（Slice 1–4：精灵窗口 + 蛋 + 双窗口 auth 同步 + 对话式 onboarding + Chat 模式（状态机 IDLE/THINKING/SPEAKING/WORKING）+ 主动陪伴接收 + 打扰档位 + 故障兜底） |
| **枢纽层**（下层） | 凭证加密落盘、WS 中转、Runner 进程编排、反向 RPC 代理、两阶段自更新、本地文件系统拦截 | **保留复用** |

两层共享同一个 Electron 主进程（CommonJS，preload contextBridge 隔离），伙伴层不直接接触凭证或 Runner 句柄——一切经枢纽层 IPC。

## 顶层目录

```
desktop/
├── main/                            # 主进程（CommonJS *.cjs）
├── renderer/                        # 渲染进程（ESM, Vite 编译）
├── scripts/                         # 构建流水线（不进 package.json#scripts）
├── assets/                          # icon.{png,ico,icns}
├── README.md                        # ← 你正在读
├── package.json / tsconfig.json / vite.config.ts / index.html / eslint.config.mjs
└── components.json / config.json    # shadcn/内部配置
```

### `main/` —— Electron 主进程

```
main/
├── entry.cjs                    # slim 应用入口（app lifecycle + 顶层协调）
├── preload.cjs                  # contextBridge → window.deskagent.*
├── lifecycle/                   # 平台差异化 + 托盘 + 窗口
│   ├── platform.cjs             #   bootstrap-platform.cjs
│   ├── tray.cjs
│   └── windows.cjs              # createSprite/createTool 抽出（若有）
├── ipc/                         # renderer↔main 通道（21 个 *.cjs）
├── backend/                     # Backend HTTP/WS 会话
│   ├── client.cjs               #   backend-client.cjs
│   ├── session.cjs              #   backend-session.cjs
│   └── ws-probe.cjs             #   gateway-ws-probe.cjs
├── runner/                      # Runner 子进程编排
│   ├── bridge.cjs               #   runner-bridge.cjs（WS server + 子进程）
│   ├── process.cjs              #   runner-process.cjs
│   ├── rpc-ws.cjs               #   runner-rpc-ws.cjs
│   ├── reverse-rpc.cjs          #   runner-reverse-rpc.cjs（Runner→LLM 代理）
│   ├── updater.cjs              #   runner-updater.cjs（两阶段）
│   └── venv.cjs                 #   venv-python.cjs（venv Python 探针）
├── security/                    # 加固 + 路径白名单 + macOS entitlements
│   ├── hardening.cjs            #   路径白名单 + 文件预览源字节上限 + 加密封存
│   ├── paths.cjs                #   DESKAGENT_HOME 等
│   └── entitlements.mac.{plist,plist.inherit}
└── shared/                       # 主进程杂项 + lib
    ├── config.cjs / utils.cjs / mime.cjs / client-context.cjs
    └── lib/{config-writer,skill-index,toolset-index}.cjs
```

### `renderer/` —— 渲染进程

```
renderer/
├── main.tsx                       # 入口：providers + installUpdaterEventListeners()
├── app.tsx                        # 角色分发（?role=sprite|tool → CompanionRoot | ToolRoot）
├── styles.css
│
├── shared/                        # 跨窗口复用：UI atom / 传输 / strings / themes / 设计系统
│   ├── components/{ui/,...}      #   设计系统 primitives（alert/button/dialog/...）
│   ├── deskagent/                 #   DeskAgentGateway class + REST config wrappers
│   ├── hooks/{use-mobile,use-media-query,use-route-enum-param}.ts
│   ├── strings/  themes/  layout/page-inset.ts  types/{deskagent.ts,global.d.ts,vite-env.d.ts}
│   ├── lib/{clipboard,haptics,icons,query-client,reconnect,storage,utils}.ts
│   ├── lib/gateway-protocol/      #   WS + JSON-RPC 客户端
│   ├── lib/{gateway-ws-url,toolset-catalog}.ts
│   └── store/{auth,gateway,haptics,notifications,version}.ts
│
├── companion/                     # 伙伴形象载体（精灵窗口）
│   ├── root.tsx                   #   CompanionRoot + GatewayBooter
│   ├── boot/                      #   useGatewayBoot + useGatewayRequest（WS 生命周期）
│   ├── sprite/                    #   sprite-stage, egg, silhouette, companion-ready
│   ├── onboarding/onboarding-flow.tsx
│   ├── proactive/{proactive,proactive-bubble}.{ts,tsx}
│   ├── chat-dock.tsx  events.ts  persona.ts(+test)  tts.ts  backend-companion-mock.ts
│   └── {chat,companion,boot}-store.ts
│
└── hub/                           # 本地枢纽（工具窗口 + IPC/orchestration）
    ├── root.tsx                   #   ToolRoot
    ├── login/login-page.tsx
    ├── overlays/{chrome,split-layout,view}.tsx
    ├── settings/                  #   14 文件（见下文）
    ├── runner/use-runner-config.ts
    └── settings-store.ts          # electron-updater atoms
```

`renderer/shared/`、`renderer/companion/`、`renderer/hub/` 三个是**同级的 renderer 子模块**，与 main.tsx / app.tsx 平级。

### `scripts/` —— 构建流水线

`assert-dist-built / assert-root-install / launch-dev-electron / stage-native-deps / test-desktop / write-build-stamp` 六个构建/测试钩子。**部分因需特权路径或私有 key（`scripts/secrets/update.{pub,key}`）不进 `package.json#scripts`**——electron-updater 验签调用与 macOS 打包前的原生依赖 staging 都需要绕开 npm 生命周期约束。

## 关键架构约束

- **双 runtime**：`main/*.cjs`（CommonJS，不经 vite/tsc）+ `renderer/**/*.{ts,tsx}`（ESM, Vite 编译）。绝不混用——main 用 `.cjs`，renderer 用 `.ts/.tsx`。
- **单 chunk 构建**：Vite 产出单 JS bundle（避免 electron-builder OOM 扫描千级 chunk）。
- **hoisted nodeLinker**：Electron 42 + pnpm 11 兼容性要求。
- **关闭按钮语义**：Win/Linux/WSL 上 close = hide to tray；macOS 上 close = hide window，Dock icon 保留。统一由 `main/lifecycle/tray.cjs::installCloseInterceptor` 实现。
- **单实例锁**：`app.requestSingleInstanceLock()` 在 `app.whenReady()` 之前调用；`second-instance` 唤起现有窗口。dev opt-out：`DESKAGENT_DESKTOP_DISABLE_SINGLE_INSTANCE_LOCK=1`。
- **Windows AppUserModelID**：`'io.deskagent.agent'`（与 `package.json#build.appId` 对齐），Windows 通知分组依赖此 ID。

## 跨模块边界（renderer 内部）

`renderer/companion/` ↔ `renderer/hub/` 是**两个窗口**而非一个工程的两个层——它们的代码历史上不该相互依赖：

| 起点 → 终点 | 许可 |
|---|---|
| `companion` → `shared` | ✓ |
| `hub` → `shared` | ✓ |
| `companion` ↔ `hub` | ✗（任何 `from '@/hub/...'` 出现在 companion 文件都会被 ESLint 拒绝） |
| `shared` → 任何 | ✗（共享层不向上依赖） |

ESLint `no-restricted-imports` 在 `renderer/companion/**` 与 `renderer/hub/**` 各设一道拦截。**唯一例外**是 `renderer/app.tsx`——这是角色分发点，需要同时 import `@companion` 的 root 和 `@hub` 的 root。

模块公共面经 `index.ts` barrel 暴露（`@/companion`、`@/hub`、`@/shared`、`@/shared/components/ui`）。模块内部细节不出 barrel——`companion/boot/useGatewayBoot` 是 companion 内部实现，不应被 hub 直接引用；若 hub 也需要类似能力，应该在 `shared/lib/` 抽出一个通用 hook。

## 托盘菜单 = 主入口

伙伴窗口刻意精简，**配置与账户动作的主入口在系统托盘右键菜单**，而非应用内 chrome。`main/lifecycle/tray.cjs::buildTrayMenu` 按 `backendSession.getSession().hasToken` 动态生成：

| 认证状态 | 菜单项 |
|---------|--------|
| 已登录 | Show DeskAgent · Settings... · Log out · Quit DeskAgent |
| 未登录 | Sign in... · Quit DeskAgent |

- **Show DeskAgent**（已登录）→ 前置精灵窗口（透明置顶常驻窗口）。
- **Sign in...**（未登录）→ 打开 framed 工具窗口，渲染 LoginGate。
- **Settings...** → 打开 framed 工具窗口，渲染 SettingsView。
- **Log out** → 向精灵窗口发 `deskagent:tray:logout`，精灵 renderer 走 logout 流 → `main/ipc/auth.cjs` 广播 `deskagent:auth:changed` 到两个窗口、并重新显示登录工具窗（精灵回蛋）。

`rebuildTrayMenu()` 在 login / logout / 启动会话恢复后重跑 `setContextMenu`。

## 通信模型

### Renderer ↔ Main（IPC）

renderer 通过 `window.deskagent.*`（preload contextBridge）调 main；main 通过 `webContents.send(...)` 推事件。每个 IPC 模块 export `registerXxxIpc(deps)`，deps 由 `main/entry.cjs` 显式注入（`backendSession` + `runnerBridge` 两个跨模块单例经 `bridgeDeps` 传递）。伙伴层新增：`deskagent:sprite:*`（点击穿透/动态置顶/工作区/休息位，`main/ipc/sprite.cjs`）与 main→**两窗口**广播 `deskagent:auth:changed`。

### Renderer ↔ Backend（REST + WS）

- **REST**：renderer 经 `window.deskagent.api({ path, method, body })` → `main/ipc/connection.cjs` 转发到 Backend（携带 JWT）。401 时发 `deskagent:auth:session-expired`。
- **WS**：`DeskAgentGateway extends JsonRpcGatewayClient`（`renderer/shared/deskagent/`），在 renderer 内直接连接 `ws://<backend>/api/chat/ws?token=<jwt>`。main 进程只通过 `deskagent:gateway:ws-url` 把 URL 推给 renderer——chat WS **不走** preload 命名空间。

### Main ↔ Runner（本地 WS）

`main/runner/bridge.cjs` 编排：`start()` → 启动 WS server（127.0.0.1:0）→ spawn Runner process（`--desktop-ws`）→ Runner 连入发 `runner_ready` → bridge 调 `get_tools` RPC 缓存结果 → emit `running`。Renderer 通过 `deskagent:runner:get-tools` IPC 拉到缓存的 Schema 后，调 `gateway.request('tools.sync', {tools})` 上报给 Backend。

### 反向 RPC（Runner → Backend）

`main/runner/reverse-rpc.cjs`：处理 Runner 的 `request_llm` → 调 Backend `POST /api/llm/completion` → 透传响应体。守卫：max 200 messages、max 1MB payload。

### 本地文件系统拦截

`renderer/companion/boot/use-gateway-request.ts::tryLocalIntercept` 在 WS 请求入口处拦截依赖本地文件系统的方法——后端在 Docker 中无法访问：

| 方法 | 拦截逻辑 |
|------|----------|
| `config.get({key:"project", cwd})` | 读 `.git/HEAD` 解析分支 |
| `complete.path({word, cwd})` | 解析 `@` 后路径前缀，列目录项 |

## Electron 二进制自更新

Desktop 走 `electron-updater` 从 Backend `/api/update` 拉取预构建安装包并原子替换。**一次更新同时刷新 desktop 二进制 + Python runner**（wheel + `server.py`），保证两端不会因版本错配而 broken。

两阶段契约（network 在前、file ops 在后，避免网络断在重启后变砖）：

- **Phase 1 — prefetch（OLD Electron 里跑）**：`main/entry.cjs::setupAutoUpdater` 在 `app.whenReady` 后挂载，延迟 30s 自检，下载 Electron 二进制；同时后台从 `/api/update/latest-runner.yml` 下载 wheel + `server.py` 到 `$DESKAGENT_HOME/runner.staging/`，验 RSA 签名 + SHA-512，写 sentinel。渲染端 "Restart now" 按钮只在 `runner-ready` 事件落地后才可点。
- **Phase 2 — install（NEW Electron 里跑）**：`app.whenReady` 早期调 `runner-updater.installPending()`：读 sentinel → `runnerBridge.stop()` 释放 Python 句柄（Windows EPERM 风险）→ `pip install --upgrade` 原地升级 → 覆盖 `server.py` → 冒烟 `import deskagent_agent, server` → `runnerBridge.start()`。
- **venv 永不被改名/移动**——只有 venv 内部 wheel 被升级；任意失败在 `finally` 块 `runnerBridge.start()` 拉起旧 runner；`attempt_count >= 3` 删 sentinel 并 emit `runner-failed {recoverable: false}`。

签名 keypair：私钥 `scripts/secrets/update.key`、公钥 `scripts/secrets/update.pub`（经 `desktop/package.json#build.extraResources` 复制到 packaged desktop，`main/runner/updater.cjs` 启动时读取校验）。**生产构建签名密钥在构建机上**——开发分支留 `update.key` 在本地是因为出包验证需要测试签名链路。

伙伴形象资产与角色定义云端持久化（[ARCHITECTURE.md §7](../ARCHITECTURE.md)），自更新只影响本地代码与运行时，不触碰用户的伙伴身份。

## 安全

- **Token 加密存储**：JWT 经 `safeStorage` 加密落盘，userData 目录权限由 OS 控制。Renderer 与 Preload 无法接触 safeStorage 接口。
- **Model Config IPC 边界**：`main/ipc/auth.cjs::deskagent:model-config:get` 返回前投影仅 `llm_*` 字段，剥离 GCS 凭据；共享 5min cache。
- **路径白名单**：`main/security/hardening.cjs` 的 `resolveReadableFileForIpc` 拒绝路径遍历、符号链接逃逸、超大读取、敏感文件（.env/.ssh/.pem 等）。
- **Runner 进程隔离**：Runner 作为独立子进程，所有工具调用经本地 WS 中转。

## 已知限制

| 限制 | 说明 |
|------|------|
| Runner 崩溃后重连窗口有限 | 端点文件 + 重连循环（~5 分钟），超时后 Runner 退出 |
| Electron 42 + pnpm 11 需 hoisted | 失去 phantom-deps 防护；等 Electron ESM 主进程支持 |
| `.cjs` + `.ts` 双 runtime | 新增 main 模块用 `.cjs`，renderer 用 `.ts/.tsx`；等 Electron ESM 主进程支持 |
| 透明窗口平台差异 | 远程显示（X11/VNC/RDP）无法合成透明层，精灵窗口降级为非透明（`SPRITE_TRANSPARENT`）；Linux 无 compositor 仍可能黑底——macOS/Windows 支持良好 |
| 托盘 Settings 中"重载 MCP"不可用 | gateway 仅在精灵窗口 boot；从托盘打开的 framed 工具窗口无 gateway，`hub/settings/mcp-settings.tsx` 的 reload 按钮优雅报"gateway 不可用"。其余 settings（runnerConfig 等 REST）不受影响 |
| WSL 下无系统托盘 | Electron Tray API 在 WSL 不可用；降级为 hide-only；托盘菜单在 WSL 下不可达 |
| 死 IPC 模块待清理 | `main/ipc/terminal.cjs`（node-pty）、`main/ipc/preview.cjs`、`main/ipc/link-title.cjs`、`main/ipc/images.cjs` 的 renderer 消费方已移除，模块仍在（node-pty 编织进 native-deps 打包链 `scripts/stage-native-deps.cjs` + `test-desktop.mjs` 断言，移除属构建管线改动） |
