# Desktop

桌面伙伴形象载体 + 本地枢纽——Electron 客户端。上层渲染常驻桌面的伙伴形象并承载陪伴式交互，下层连接云端 Backend 与本地 Runner，持有用户凭证、中转工具调用、管理 Runner 生命周期与自更新。

设计文档：[design.md](../design.md) §2 / §3 / §5 / §7 / §10；伙伴层交互设计见 [plan.md](plan.md)。

## 双层定位

DeskAgent 是**双层叠加**的单 Electron 应用：

| 层 | 职责 | 状态 |
|----|------|------|
| **伙伴层**（上层） | 桌面精灵形象渲染、onboarding（蛋→角色定义→孵化）、陪伴式交互 UI | **实现中**（MVP Slice 1–3：精灵窗口 + 蛋 + 双窗口 auth 同步 + 对话式 onboarding + Chat 模式（文字/粘贴图片，状态机 IDLE/THINKING/SPEAKING/WORKING）已落地；主动陪伴 / 打扰档位 / 分级故障兜底见 [plan.md](plan.md) §7 路线） |
| **枢纽层**（下层） | 凭证加密落盘、WS 中转、Runner 进程编排、反向 RPC 代理、两阶段自更新、本地文件系统拦截 | **保留复用** |

两层共享同一个 Electron 主进程，职责严格分离：枢纽层处理协议与安全，伙伴层处理形象渲染与用户体验。伙伴层不直接接触凭证或 Runner 句柄，一切经枢纽层 IPC。

## 架构地图

```
desktop/
├── electron/     # 枢纽层（主进程，CommonJS *.cjs）——lifecycle / tray / backend-session / runner-bridge / runner-updater / ipc/（含 sprite.cjs 精灵窗口控制）
├── src/          # 渲染进程（ESM，Vite 编译）——gateway hooks / settings / login / overlays / store / app/companion（伙伴层）
├── scripts/      # 构建钩子（before-pack / notarize / stage-native-deps / test-desktop）
└── assets/       # icon.{png,ico,icns}
```

依赖方向：`src/` 渲染层经 `window.deskagent.*`（preload contextBridge）调 `electron/` 主进程；主进程不 import 渲染层代码。

## 关键架构约束

- **双 runtime**：`electron/*.cjs`（CommonJS，不经 vite/tsc）+ `src/**/*.ts`（ESM，Vite 编译）。绝不混用——main 进程用 `.cjs`，renderer 用 `.ts/.tsx`。
- **单 chunk 构建**：Vite 产出单 JS bundle（避免 electron-builder OOM 扫描千级 chunk）。
- **hoisted nodeLinker**：Electron 42 + pnpm 11 兼容性要求。
- **关闭按钮语义**：Win/Linux/WSL 上 close = hide to tray；macOS 上 close = hide window，Dock icon 保留。统一由 `tray.cjs::installCloseInterceptor` 实现。
- **单实例锁**：`app.requestSingleInstanceLock()` 在 `app.whenReady()` 之前调用；`second-instance` 唤起现有窗口。dev opt-out：`DESKAGENT_DESKTOP_DISABLE_SINGLE_INSTANCE_LOCK=1`。
- **Windows AppUserModelID**：`'io.deskagent.agent'`（与 `package.json#build.appId` 对齐），Windows 通知分组依赖此 ID。

## 托盘菜单 = 主入口

伙伴窗口刻意精简，**配置与账户动作的主入口在系统托盘右键菜单**，而非应用内 chrome。`tray.cjs::buildTrayMenu` 按 `backendSession.getSession().hasToken` 动态生成：

| 认证状态 | 菜单项 |
|---------|--------|
| 已登录 | Show DeskAgent · Settings... · Log out · Quit DeskAgent |
| 未登录 | Sign in... · Quit DeskAgent |

- **Show DeskAgent**（已登录）→ 前置精灵窗口（透明置顶常驻窗口）。
- **Sign in...**（未登录）→ 打开 framed 工具窗口，渲染 LoginGate。
- **Settings...** → 打开 framed 工具窗口，渲染 SettingsView（工具窗口按 `$auth` 自选登录/设置态）。
- **Log out** → 向精灵窗口发 `deskagent:tray:logout`，精灵 renderer 走 logout 流 → `ipc/auth.cjs` 广播 `deskagent:auth:changed` 到两个窗口、并重新显示登录工具窗（精灵回蛋）。

`rebuildTrayMenu()` 在 login / logout / 启动会话恢复后重跑 `setContextMenu`，保证菜单标签与实时会话状态一致（`ipc/auth.cjs` 经 `bridgeDeps.rebuildTrayMenu` 调用）。

## 伙伴层（实现中）

**双窗口模型**：精灵窗口（透明、无边框、置顶、可控点击穿透）是唯一常驻主窗口；framed 工具窗口按需承载 Login / Settings。两者共享同一渲染 bundle，靠 URL `?role=sprite|tool` 在根组件分流（`app/index.tsx`）。

- **精灵窗口**（`createSpriteWindow`）铺满工作区、默认全窗点击穿透（`setIgnoreMouseEvents(true,{forward:true})`）；渲染层 `SpriteStage` 对 mouse-move hit-test，光标进入形象/对话框时请求捕获、离开时释放。形象为代码渲染（SVG/CSS）——蛋 + 累计裂纹 + 犯困/醒来态；Backend 生成的 portrait / WebM clip 尚未接入（[plan.md §1.3](plan.md)）。
- **gateway 门控**：仅精灵窗口、且仅已鉴权后 boot gateway（`CompanionRoot` 条件渲染 `GatewayBooter` → `useGatewayBoot`）。未鉴权 = 蛋 teaser，不连 WS——避免 `useGatewayBoot` 在无 JWT 时抛错或触发 1008→logout 破坏首启。
- **跨窗口 auth 同步**：两个 BrowserWindow = 两个独立渲染进程 = 两份独立 nanostores，`$auth` 不共享。`ipc/auth.cjs` 在 login/logout/refresh 后经 `bridgeDeps.broadcastAuthChanged` 向**两窗口**广播 `deskagent:auth:changed`（镜像 `onSessionExpired` 机制），各窗口根订阅后更新各自副本：精灵据此 boot/teardown gateway、推进生命周期；工具窗口据此切 LoginGate/SettingsView。登录成功隐藏工具窗、登出/过期重新显示。
- **动态置顶**：对话框激活且聚焦时精灵窗口降级 `alwaysOnTop`（切到别的 app 可被覆盖），关闭恢复（[plan.md §1.2](plan.md)；chat 未实现前不触发）。

详细交互设计（onboarding、动画状态机、场景库、陪伴范式、渐进路线）见 [plan.md](plan.md)；跨模块表达层契约（情绪 cue、clip 生命周期、onboarding 断点恢复）见 [design.md §5.2.IV / §7.5](../design.md)。

`handleGatewayEvent`（`use-gateway-boot`）目前仍为空实现——chat / 主动陪伴事件分发在后续 slice 接入；精灵窗口的 WS 事件流是它们的落点。

## 通信模型

### Renderer ↔ Main（IPC）

renderer 通过 `window.deskagent.*` 调 main，后者通过 `webContents.send(...)` 推事件。`preload.cjs` 是白名单——只有显式 expose 的方法可调。每个 IPC 模块 export `registerXxxIpc(deps)`，deps 由 `main.cjs` 显式注入（`backendSession` + `runnerBridge` 两个跨模块单例经 `bridgeDeps` 传递）。伙伴层新增：`deskagent:sprite:*`（点击穿透/动态置顶/工作区/休息位，`ipc/sprite.cjs`）与 main→**两窗口**广播 `deskagent:auth:changed`（跨渲染器 auth 同步，见上）。

### Renderer ↔ Backend（REST + WS）

- **REST**：renderer 经 `window.deskagent.api({ path, method, body })` → `connection.cjs` 转发到 Backend（携带 JWT）。401 时发 `deskagent:auth:session-expired`。
- **WS**：`DeskAgentGateway extends JsonRpcGatewayClient`，在 renderer 内直接连接 `ws://<backend>/api/chat/ws?token=<jwt>`。main 进程只通过 `deskagent:gateway:ws-url` 把 URL 推给 renderer——chat WS **不走** preload 命名空间。

### Main ↔ Runner（本地 WS）

`runner-bridge.cjs` 编排：`start()` → 启动 WS server（127.0.0.1:0）→ spawn Runner process（`--desktop-ws`）→ Runner 连入发 `runner_ready` → bridge 调 `get_tools` RPC 缓存结果 → emit `running`。Renderer 通过 `deskagent:runner:get-tools` IPC 拉到缓存的 Schema 后，调 `gateway.request('tools.sync', {tools})` 上报给 Backend。

### 反向 RPC（Runner → Backend）

`runner-reverse-rpc.cjs`：处理 Runner 的 `request_llm` → 调 Backend `POST /api/llm/completion` → 透传响应体。守卫：max 200 messages、max 1MB payload。

### 本地文件系统拦截

`use-gateway-request.ts::tryLocalIntercept` 在 WS 请求入口处拦截依赖本地文件系统的方法——后端在 Docker 中无法访问：

| 方法 | 拦截逻辑 |
|------|----------|
| `config.get({key:"project", cwd})` | 读 `.git/HEAD` 解析分支 |
| `complete.path({word, cwd})` | 解析 `@` 后路径前缀，列目录项 |

## Electron 二进制自更新

Desktop 走 `electron-updater` 从 Backend `/api/update` 拉取预构建安装包并原子替换。**一次更新同时刷新 desktop 二进制 + Python runner**（wheel + `server.py`），保证两端不会因版本错配而 broken。

两阶段契约（network 在前、file ops 在后，避免网络断在重启后变砖）：

- **Phase 1 — prefetch（OLD Electron 里跑）**：`main.cjs::setupAutoUpdater` 在 `app.whenReady` 后挂载，延迟 30s 自检，下载 Electron 二进制；同时后台从 `/api/update/latest-runner.yml` 下载 wheel + `server.py` 到 `$DESKAGENT_HOME/runner.staging/`，验 RSA 签名 + SHA-512，写 sentinel。渲染端 "Restart now" 按钮只在 `runner-ready` 事件落地后才可点。
- **Phase 2 — install（NEW Electron 里跑）**：`app.whenReady` 早期调 `runnerUpdater.installPending()`：读 sentinel → `runnerBridge.stop()` 释放 Python 句柄（Windows EPERM 风险）→ `pip install --upgrade` 原地升级 → 覆盖 `server.py` → 冒烟 `import deskagent_agent, server` → `runnerBridge.start()`。
- **venv 永不被改名/移动**——只有 venv 内部 wheel 被升级；任意失败在 `finally` 块 `runnerBridge.start()` 拉起旧 runner；`attempt_count >= 3` 删 sentinel 并 emit `runner-failed {recoverable: false}`。

签名 keypair：私钥 `scripts/secrets/update.key`、公钥 `scripts/secrets/update.pub`（经 `desktop/package.json#build.extraResources` 复制到 packaged desktop，`runner-updater.cjs` 启动时读取校验）。

伙伴形象资产与角色定义云端持久化（[design.md §7](../design.md)），自更新只影响本地代码与运行时，不触碰用户的伙伴身份。

## 安全

- **Token 加密存储**：JWT 经 `safeStorage` 加密落盘，userData 目录权限由 OS 控制。Renderer 与 Preload 无法接触 safeStorage 接口。
- **Model Config IPC 边界**：`deskagent:model-config:get` 返回前投影仅 `llm_*` 字段，剥离 GCS 凭据；共享 5min cache。
- **路径白名单**：`hardening.cjs` 的 `resolveReadableFileForIpc` 拒绝路径遍历、符号链接逃逸、超大读取、敏感文件（.env/.ssh/.pem 等）。
- **Runner 进程隔离**：Runner 作为独立子进程，所有工具调用经本地 WS 中转。

## 已知限制

| 限制 | 说明 |
|------|------|
| Runner 崩溃后重连窗口有限 | 端点文件 + 重连循环（~5 分钟），超时后 Runner 退出 |
| Electron 42 + pnpm 11 需 hoisted | 失去 phantom-deps 防护；等 Electron ESM 主进程支持 |
| `.cjs` + `.ts` 双 runtime | 新增 main 模块用 `.cjs`，renderer 用 `.ts/.tsx`；等 Electron ESM 主进程支持 |
| 透明窗口平台差异 | 远程显示（X11/VNC/RDP）无法合成透明层，精灵窗口降级为非透明（`SPRITE_TRANSPARENT`）；Linux 无 compositor 仍可能黑底——macOS/Windows 支持良好。完整 sprite-sheet 降级留后续阶段 |
| 托盘 Settings 中"重载 MCP"不可用 | gateway 仅在精灵窗口 boot；从托盘打开的 framed 工具窗口无 gateway，`mcp-settings.tsx` 的 reload 按钮优雅报"gateway 不可用"。其余 settings（runnerConfig 等 REST）不受影响 |
| WSL 下无系统托盘 | Electron Tray API 在 WSL 不可用；降级为 hide-only；托盘菜单在 WSL 下不可达 |
| 死 IPC 模块待清理 | `ipc/terminal.cjs`（node-pty）、`ipc/preview.cjs`、`ipc/link-title.cjs`、`ipc/images.cjs` 的 renderer 消费方已移除，模块仍在（node-pty 编织进 native-deps 打包链 `scripts/stage-native-deps.cjs` + `test-desktop.mjs` 断言，移除属构建管线改动） |
