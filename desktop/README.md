# Desktop

桌面伙伴形象载体 + 本地枢纽——单 Electron 应用，承担双职责。

设计文档：[ARCHITECTURE.md](../ARCHITECTURE.md) §2 / §4 / §6 / §9；伙伴层详细交互见 [COMPANION_DESIGN.md](../COMPANION_DESIGN.md)。

## 双层定位

DeskAgent 是**双层叠加**的单 Electron 应用：

| 层 | 职责 | 状态 |
|----|------|------|
| **伙伴层**（上层） | 桌面精灵形象渲染、onboarding（蛋→13 步角色定义含形象/物种/性别 + speaking_style → 孵化）、陪伴式交互 UI | MVP 已落地：精灵窗口 + 蛋 + 双窗口 auth 同步 + 对话式 onboarding + Chat 模式 + Voice Call 模式 + 长期记忆管理 + 主动陪伴 + 故障兜底 + 角色管理 + 资产 3 档降级（程序化 → sprite → video）|
| **枢纽层**（下层） | 凭证加密落盘、WS 中转、Runner 进程编排、反向 RPC 代理、两阶段自更新、本地文件系统拦截 | **保留复用** |

两层共享同一个 Electron 主进程（CommonJS，preload contextBridge 隔离），伙伴层不直接接触凭证或 Runner 句柄——一切经枢纽层 IPC。

## 顶层目录

`main/`（CommonJS *.cjs）+ `renderer/`（ESM *.{ts,tsx}，Vite 编译）两个 runtime，绝不混用。`renderer/` 内 `shared` / `companion` / `hub` 三子模块同级，与 `main.tsx` / `app.tsx` 平级，互不跨引（ESLint `no-restricted-imports` 拦截 `companion`↔`hub`，详见"跨模块边界"）。`scripts/` 是构建/测试钩子（不进 `package.json#scripts`）。`assets/` 放 icon。

### `scripts/` —— 构建流水线

构建/测试钩子。**因需特权路径或私有 key（`scripts/secrets/update.{pub,key}`）不进 `package.json#scripts`**——electron-updater 验签调用与 macOS 打包前的原生依赖 staging 都需要绕开 npm 生命周期约束。

## 关键架构约束

- **双 runtime**：`main/*.cjs`（CommonJS，不经 vite/tsc）+ `renderer/**/*.{ts,tsx}`（ESM, Vite 编译）。绝不混用——main 用 `.cjs`，renderer 用 `.ts/.tsx`。
- **单 chunk 构建**：Vite 产出单 JS bundle（避免 electron-builder OOM 扫描千级 chunk）。
- **hoisted nodeLinker**：Electron 42 + pnpm 11 兼容性要求。
- **关闭按钮语义**：Windows 上 close = hide to tray；macOS 上 close = hide window、Dock icon 保留。统一由 `main/lifecycle/tray.cjs::installCloseInterceptor` 实现。
- **单实例锁**：`app.requestSingleInstanceLock()` 在 `app.whenReady()` 之前调用；`second-instance` 唤起现有窗口。dev opt-out：`DESKAGENT_DESKTOP_DISABLE_SINGLE_INSTANCE_LOCK=1`。
- **Windows AppUserModelID**：`'io.deskagent.agent'`（与 `package.json#build.appId` 对齐），Windows 通知分组依赖此 ID。
- **精灵窗口透明需要双重保证**：BrowserWindow `transparent: true` **加** 渲染层 `body` 透明（`html[data-role='sprite'] body { background: transparent }`，`data-role` 由 `index.html` 内嵌脚本在 `<head>` 解析时同步设置）。两者缺一，body 背景色（`var(--ui-chat-surface-background)`）会在桌面剩余区域盖满屏幕——违背"伙伴应不干扰用户正常工作"的契约。
- **交互范围仅限可见矩形**：Electron 的 `setIgnoreMouseEvents` 是窗口级二元开关；要在屏幕尺寸的透明窗口里只让"看得见"的区域捕获、其余继续穿透给桌面其它应用，所有 overlay（`SpriteStage` / `ChatDock` / `VoiceCallDock` / `CompanionSettings` / `OnboardingFlow`）把自己的面板 bbox 注册到 `companion/interactive-regions.ts`，由 `SpriteStage` 的全局 `mousemove` 唯一做命中测试再切换 `setIgnoreMouseEvents`。任何 overlay 都不能再用 `setIgnoreMouseEvents({ignore:false})` 一刀切捕获整个窗口——那会立刻把桌面的其它应用"锁死"。

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

伙伴窗口刻意精简，**配置与账户动作的主入口在系统托盘右键菜单**，而非应用内 chrome。菜单按 `backendSession.getSession().hasToken` 动态生成（读 `main/lifecycle/tray.cjs::buildTrayMenu` 即得）。

跨文件契约：**Log out** 向精灵窗口发 `deskagent:tray:logout` → 精灵 renderer 走 logout 流 → `main/ipc/auth.cjs` 广播 `deskagent:auth:changed` 到两个窗口、并重新显示登录工具窗（精灵回蛋）。

`rebuildTrayMenu()` 在 login / logout / 启动会话恢复后重跑 `setContextMenu`。

## 通信模型

### Renderer ↔ Main（IPC）

renderer 通过 `window.deskagent.*`（preload contextBridge）调 main；main 通过 `webContents.send(...)` 推事件。所有 IPC 命名空间以 `deskagent:` 为前缀（`deskagent:sprite:*` 点击穿透/动态置顶/工作区/休息位；main→**两窗口**广播 `deskagent:auth:changed` 等）。

### Renderer ↔ Backend（REST + WS）

- **REST**：renderer 经 `window.deskagent.api({ path, method, body })` → `main/ipc/connection.cjs` 转发到 Backend（携带 JWT）。401 时发 `deskagent:auth:session-expired`。
- **WS**：`DeskAgentGateway extends JsonRpcGatewayClient`（`renderer/shared/deskagent/`），在 renderer 内直接连接 `ws://<backend>/api/chat/ws?token=<jwt>`。main 进程只通过 `deskagent:gateway:ws-url` 把 URL 推给 renderer——chat WS **不走** preload 命名空间。

### Main ↔ Runner（本地 WS）

`main/runner/bridge.cjs` 编排 Runner 子进程（本地 WS，127.0.0.1:0）。Renderer 通过 `deskagent:runner:get-tools` IPC 拉到 bridge 缓存的 tool schema 后，调 `gateway.request('tools.sync', {tools})` 上报给 Backend。

### 反向 RPC（Runner → Backend）

`main/runner/reverse-rpc.cjs`：处理 Runner 的 `request_llm` → 调 Backend `POST /api/llm/completion` → 透传响应体。守卫：max 200 messages、max 1MB payload。

### 打扰档位的权威边界

**Desktop 是 disturbance tier 的唯一权威**（[ARCHITECTURE.md §5](../ARCHITECTURE.md)）：`$userPreferredTier` 持久化在 `localStorage`、`$effectiveTierOverride` 由活动感知器（`companion/activity.ts`）写入、`$effectiveTier` 是 computed 派生。Desktop 每次有效值变化都经 `companion.set_disturbance_tier` 单向 push 给 Backend，**Backend 端 `_disturbance[user_id]` 只是过程镜像，不独立推导**——服务端 gate（`send_message_tool` / `cron._kick_autonomous_turn`）读镜像，但用户偏好与活动上下文只在 Desktop 持有。WS 重连后 Desktop 立刻再推一次同步（`use-gateway-boot.syncDisturbanceTier`）。这条边界让 Backend 重启或 WS 短暂掉线时也不漂移用户意图，也防止 LLM 在 server-side 推导时绕过用户的手动 quiet 选择。

### STT/TTS 引擎路由

`main/ipc/media.cjs` 是 `media.stt` / `media.tts` 的唯一路由点，在 Backend 云端引擎与 Runner 本地引擎之间决策。**默认本地优先**（本地引擎零成本），三档偏好由 backend config 的 `stt.engine` / `tts.engine`（`auto` / `local` / `cloud`）控制，经短 TTL 缓存读取：

| 档位 | 行为 |
|------|------|
| `auto`（默认） | 本地优先;本地不可用或失败 → 回退云端 |
| `local` | 纯本地;失败/不可用 → 抛错,不回退(由 renderer 兜底:STT 提示"没听清"、TTS 退纯文字) |
| `cloud` | 永远云端 |

**STT `silent_fallback`**（`stt.silent_fallback`，默认 `true`）：仅作用于 `auto` 档下"本地跑出了弱/错误结果"的回退。`true`（默认）静默改跑云端，用户无感；`false` 则把本地弱结果直接暴露给 renderer（隐私/成本敏感用户不想偷偷走云端）。"本地引擎整体不可用"（runner 未连 / 工具未注册）仍照常回退云端——那是 `auto` 的核心承诺，不属于"弱结果"，不受此开关影响。**TTS 回退**：本地链 piper → pyttsx3 任一失败静默下一引擎；TTS `auto` 链跑完后**才**抛 `Set tts.engine=cloud` 提示——即 `auto` 模式下 TTS 不区分"弱结果"，回退链路始终静默。日志结构化 `stt#N`/`tts#N` 行的 `silent_fallback_used` 字段（commit 90cab03）让 dev 端能 grep 静默回退的实际发生频次。

本地可用性由 runner 工具 schema 决定——`speech_to_text` / `text_to_speech` 是否出现在 `runnerBridge.getTools()`(已被 runner `check_fn` 过滤)。media.cjs 在路由层桥接两侧契约:STT 把 dataUrl 解码为 base64 喂给 runner;TTS 把 runner 产出的本地 WAV 路径读回转 dataUrl。renderer 的 `media.*` 接口因此不感知路由。

**voice id 不跨引擎**：云端 voice id（provider 目录中的 id）与本地 voice id（Piper `en_US-amy-medium` 格式）属于不同命名空间。`media.cjs` 路由到本地时不传 caller 的 voice——Piper 用 `config.yaml::audio.tts.default_voice` 自行决定音色；路由到云端时才透传 caller 的 voice id。用户在伙伴设置中选的音色仅在云端路径生效。Voice 设计 token `mimo_voicedesign:<prompt>` 路由到 `cloud`（`media.cjs:300`）因为 Piper 解析不动这种自描述 token。

**STT/TTS 引擎选择不在 Desktop 设置面板暴露**：三档（`auto` / `local` / `cloud`）+ `stt.silent_fallback` 走 Backend 配置（`stt.engine` / `tts.engine`），由 `main/ipc/media.cjs` 在 IPC 边界读 short-TTL 缓存决策。伙伴设置面板只管"音色"——voice_id 选择、试听、按 `tts.match_voice` / `tts.design_voice` 切档；引擎路由策略属于运维/部署侧决策（用户在自托管/企业场景下要可控可通过 Backend `config.set` JSON-RPC 写全局 setting，不暴露在 sprite UI）。

**Voice picker 语言 tabs**（`onboarding-flow.tsx` 语音预览阶段）：三个 tab——`中文` / `English` / `全部`。默认 `中文`（产品方向"默认中文"）。点击 tab → `fetchVoiceCatalog(requestGateway, lang)` 重新拉取后端 `/api/media/tts.list_voices`（带 `language` 过滤参数），用 zh-first 排序的子集刷新 `voiceCatalog` 列表。catalog 空时回退到 `DEFAULT_VOICE`。

**音色目录页**（`hub/settings/voice-gallery-settings.tsx`，托盘 Settings → 音色目录）：只读浏览 + 试听当前云端 provider 的全部音色。framed 工具窗口无 gateway，调不到 `tts.list_voices` JSON-RPC，故走 REST `GET /api/companion/voices`（与 gateway 方法复用同一 `list_tts_voices` 服务）；试听走 `deskagent:media:tts` IPC（两窗口皆可用）。**只读**——更换伙伴音色仍在精灵窗口的「伙伴设置」里进行。缓存于 `localStorage` 的 companion voice id 失效（provider 目录变化 / 用户切换 provider）时，精灵窗口就绪后经 `companion/voice-validity.ts` 检测并弹 warning 通知引导重选（commit eebf53c 区分 `fetch_failed` 静默 vs `catalog_miss` 提示；后端 `pick_voice_id` 已容错未知 id，TTS 不会断）。

**Dev 终端 trace**：`media.cjs` 在每次 STT/TTS 请求收尾时发**一行**结构化日志（`[tts#N] done …` / `[stt#N] done …`），auto-fallback 多一行 `[tts#N] fallback from=local to=cloud reason=…`。关键字段：`voice_in`（caller 想用的）、`engine_pref`（用户配置）、`route`（local/cloud）、`engine`（piper/pyttsx3/cloud）、`voice`（实际用的 voice id，**这里就能看到本地路径静默把 cloud voice id 丢成 Piper config 默认**）、`voice_out`（云端实际收到的）、`mime` / `ms` / `context` / `language`（STT 透传给 `/api/media/stt` 的 `language` 字段，默认 `zh`）。Renderer 调 `speak(text, voice?, context?)` 时可传一个短标签（`onboarding.q2` / `onboarding.voice.preview.try` 等），trace 行的 `context` 字段会带上。日志由 `entry.cjs` 注入的 `log: rememberLog` 落到 `desktop.log` 并镜像到 `pnpm dev` 的终端。

## Electron 二进制自更新

Desktop 走 `electron-updater` 从 Backend `/api/update` 拉取预构建安装包并原子替换。**一次更新同时刷新 desktop 二进制 + Python runner**（wheel + `server.py`），保证两端不会因版本错配而 broken。

两阶段契约（network 在前、file ops 在后，避免网络断在重启后变砖）：

- **Phase 1 — prefetch（OLD Electron 里跑）**：`main/entry.cjs::setupAutoUpdater` 在 `app.whenReady` 后挂载，延迟 30s 自检，下载 Electron 二进制；同时后台从 `/api/update/latest-runner.yml` 下载 wheel + `server.py` 到 `$DESKAGENT_HOME/runner.staging/`，验 RSA 签名 + SHA-512，写 sentinel。渲染端 "Restart now" 按钮只在 `runner-ready` 事件落地后才可点。
- **Phase 2 — install（NEW Electron 里跑）**：`runner-updater.installPending()` 读 sentinel 后原地升级 wheel 与 `server.py`，冒烟 `import deskagent_agent, server` 后重启 bridge。**`runnerBridge.stop()` 必须在 pip install 前**——释放 Python 句柄避免 Windows EPERM。**降级语义**：pip / smoke / start 任一失败 → rollback marker（升级前 `pip show` 快照的 `Name==Version`）还原 site-packages → emit `runner-failed {recoverable: true}`；`attempt_count >= 3` → 删 sentinel，emit `runner-failed {recoverable: false}`。对应 ARCH §9 "失败 → 降级到旧版 Runner 并向用户警告"。
- **venv 永不被改名/移动**——只有 venv 内部 wheel 被升级。

签名 keypair：私钥 `scripts/secrets/update.key`、公钥 `scripts/secrets/update.pub`（经 `desktop/package.json#build.extraResources` 复制到 packaged desktop，`main/runner/updater.cjs` 启动时读取校验）。**生产构建签名密钥在构建机上**——开发分支留 `update.key` 在本地是因为出包验证需要测试签名链路。

伙伴形象资产与角色定义云端持久化（[ARCHITECTURE.md §6](../ARCHITECTURE.md)），自更新只影响本地代码与运行时，不触碰用户的伙伴身份。

## 安全

- **Token 加密存储**：JWT 经 `safeStorage` 加密落盘，userData 目录权限由 OS 控制。Renderer 与 Preload 无法接触 safeStorage 接口。
- **Installer → desktop one-shot auth handoff**：安装器登录成功后写 `$DESKAGENT_HOME/agent-session-bootstrap.json`（schemaVersion=1：raw jwt、baseUrl、tokenExpiresAt、user、savedAt）。Desktop 主进程在 `restoreSession()` 后、`autoStartBridge` 前通过 `main/backend/bootstrap-session.cjs::consumeBootstrapSession` 消费该文件：原子重命名为 `.consumed` → POST `${baseUrl}/api/user/refresh` 校验 token → `BackendSession::adoptSession` 走 safeStorage 落盘到 `agent-session.json` → 广播 `deskagent:auth:changed` 让 sprite 自动 boot gateway。任何失败（schema 不匹配、refresh 401、网络断）都静默删文件，回退到未登录态。路径可被 `DESKAGENT_DESKTOP_BOOTSTRAP_SESSION` 覆盖（测试用）。密码永不被持久化。
- **Auth bootstrap 一致性**：用户登录成功的 baseUrl 写入 `$DESKAGENT_HOME/desktop-config.json`（POSIX 0600；Windows 由父目录 ACL 控制）；登出只清 `agent-session.json`，不动 desktop-config.json，所以登录页每次都会预填上次的 baseUrl 而 token 永远不会被还原。优先级：persisted `desktop-config.json` > bundled `config.json` > `null`。
- **Model Config IPC 边界**：`main/ipc/auth.cjs::deskagent:model-config:get` 返回前投影仅 `llm_*` 字段，剥离 GCS 凭据；共享 5min cache。
- **路径白名单**：`main/security/hardening.cjs` 的 `resolveReadableFileForIpc` 拒绝路径遍历、符号链接逃逸、超大读取、敏感文件（.env/.ssh/.pem 等）。
- **Runner 进程隔离**：Runner 作为独立子进程，所有工具调用经本地 WS 中转。

## 已知限制

| 限制 | 说明 |
|------|------|
| Runner 崩溃后重连窗口有限 | 端点文件 + 重连循环（~5 分钟），超时后 Runner 退出 |
| `voice-call-dock.tsx` useEffect 依赖故意省略 `[gatewayState]` | 麦克风挂载/take-down 由 `[requestGateway]` 触发；reconnect 重入若再加 `gatewayState` 会再次重新挂麦克风导致当前通话被打断。代码注释内已说明，依赖列表是当下决策。 |
| Electron 42 + pnpm 11 需 hoisted | 失去 phantom-deps 防护；等 Electron ESM 主进程支持 |
| `.cjs` + `.ts` 双 runtime | 新增 main 模块用 `.cjs`，renderer 用 `.ts/.tsx`；等 Electron ESM 主进程支持 |
| 透明窗口平台差异 | 远程显示（X11/VNC/RDP）无法合成透明层，精灵窗口降级为非透明（`SPRITE_TRANSPARENT`）；macOS / Windows 本地会话支持良好 |
| 托盘 Settings 中"重载 MCP"不可用 | gateway 仅在精灵窗口 boot；从托盘打开的 framed 工具窗口无 gateway，`hub/settings/mcp-settings.tsx` 的 reload 按钮优雅报"gateway 不可用"。其余 settings（runnerConfig 等 REST）不受影响 |
| Windows 单实例锁 dev opt-out | `DESKAGENT_DESKTOP_DISABLE_SINGLE_INSTANCE_LOCK=1` 强制多实例运行，便于并行调试窗口 |
