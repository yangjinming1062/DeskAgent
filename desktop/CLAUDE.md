# Desktop

桌面枢纽——Electron 客户端，连接云端 Backend 与本地 Runner。不持有 LLM 配置，不执行工具。

设计文档：[design.md](../design.md) §2.2 / §3.2 / §4

## 设计意图

- **UI 收敛**：登录后全部配置从 Backend 拉取，Desktop 不持有任何 LLM 凭据（provider/model/API key 全部由 Backend 决定并由管理员经 `/api/admin/{user_id}/model-config` 管理）。Settings → Account 集中暴露用户可在自身作用域内改写的 `user_settings` 字段——目前含 web search 后端与 API key（`web.*`，敏感字段在 GET 时被后端剥离为 `*_set` / `*_fingerprint`，原值永不返回）、agent 默认行为（`agent.{reasoning_effort, service_tier, yolo_mode, enable_background_review}`），以及显示偏好 `display.show_subagents_in_sidebar`（控制子代理会话是否出现在侧栏 recents 中）。Save 按钮统一提交一次 PUT
- **Runner 管理**：登录后自动启动 Runner 进程，传递 `--desktop-ws` 参数
- **工具中转**：Backend 下发的 `tool.call` 事件经 renderer 通过 IPC 调 Runner，结果经 `tool.result` JSON-RPC 回传 Backend
- **安装/卸载**：Tauri `Zast-Setup` 负责首次安装与卸载
- **Electron 二进制自更新**：Desktop 通过 `electron-updater` 从 Backend `/api/update` 拉取预构建产物自更新(desktop 二进制 + Python runner wheel + `server.py`,两半一起装),30s 后台自检 + 状态栏徽标 + Settings → About 手动按钮

## 当前架构

```
desktop/
├── assets/                      # icon.{png,ico,icns}（三平台共用）
├── electron/                    # 主进程（CommonJS，Electron 42 不支持 ESM）
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
│   ├── ipc/                     # 19 个 IPC 命名空间，每个 export registerXxxIpc(deps)
│   │   ├── auth.cjs             # zast:auth:login/logout/session/change-password + zast:model-config:get
│   │   ├── connection.cjs       # zast:connection + REST 代理
│   │   ├── runner.cjs           # zast:runner:* + auto-start/stop bridge on auth events
│   │   ├── terminal.cjs         # zast:terminal:* (node-pty)
│   │   ├── files.cjs            # zast:readFileDataUrl / readFileText / selectPaths
│   │   ├── fs.cjs               # zast:fs:readDir / gitRoot
│   │   ├── clipboard.cjs        # zast:writeClipboard / saveClipboardImage
│   │   ├── images.cjs           # zast:saveImageFromUrl / saveImageBuffer
│   │   ├── link-title.cjs       # zast:fetchLinkTitle (curl + hidden BrowserWindow)
│   │   ├── preview.cjs          # zast:normalizePreviewTarget / watchPreviewFile / stopPreviewFileWatch
│   │   ├── runner-config.cjs    # zast:runner-config:read/write
│   │   ├── settings.cjs         # zast:setting:defaultProjectDir:*
│   │   ├── system.cjs           # zast:requestMicrophoneAccess / notify / version
│   │   ├── titlebar.cjs         # zast:titlebar-theme
│   │   ├── external.cjs         # zast:openExternal
│   │   ├── recorder.cjs         # zast:recorder:* + multimodal chat attachment
│   │   ├── skills.cjs           # zast:skills:list / zast:skill:set-enabled
│   │   ├── media.cjs            # zast:media:stt / zast:media:tts（multipart 上传 + 流下载）
│   │   └── update.cjs           # zast:update:check/download/install/status + 事件转发
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
│   ├── zast/                    # REST helpers + ZastGateway class（7 文件）
│   │   ├── _types.ts            # 共享类型定义
│   │   ├── gateway.ts           # ZastGateway（WS 连接）
│   │   ├── sessions.ts          # /api/sessions/*
│   │   ├── config.ts            # /api/config
│   │   ├── insights.ts          # /api/insights/overview
│   │   ├── status.ts            # /api/status
│   │   └── index.ts             # barrel + audio stubs（transcribeAudio/speakText）
│   ├── lib/                     # 跨 app 复用模块
│   │   └── gateway-protocol/    # JsonRpcGatewayClient
│   ├── store/                   # nanostores atoms + computed；下列是跨子包 contract 的核心 store，叶子 feature store 不在此列出
│   │   ├── auth.ts              # $auth: pending | unauthenticated | authenticated
│   │   ├── gateway.ts           # $gateway: ZastGateway 实例
│   │   └── session.ts           # $sessions / $activeSessionId / $messages / $busy 等
│   ├── app/                     # 路由级 feature 模块；下列是顶层路由，叶子 feature 模块（chat/session/settings/toolbar 等）不在此列出
│   │   ├── login-gate.tsx       # 认证门控
│   │   ├── desktop-controller.tsx  # 登录后主控制器
│   │   ├── routes.ts            # 路由定义
│   │   └── insights/            # /insights 使用洞察仪表板
│   ├── components/              # 跨 feature 复用 UI
│   ├── hooks/                   # 通用 hooks
│   ├── i18n/                    # 翻译（en / zh,默认 zh）
│   ├── themes/                  # 6 主题 + CSS variables
│   ├── types/                   # 共享类型
│   └── styles.css               # Tailwind v4 + 自定义 token
├── scripts/                     # 构建钩子
├── config.json                  # 后端 URL 默认值
└── package.json                 # Electron 42 / React 19 / pnpm 11
```

### 关键架构决策

- **双 runtime**：`electron/*.cjs`（CommonJS，不经 vite/tsc）+ `src/**/*.ts`（ESM，Vite 编译）。绝不混用——main 进程用 `.cjs`，renderer 用 `.ts/.tsx`
- **单 chunk 构建**：Vite 产出 ~22MB 单 JS bundle（避免 electron-builder OOM 扫描千级 chunk）
- **hoisted nodeLinker**：Electron 42 + pnpm 11 兼容性要求
- **Backend URL 来源**：`electron/config.cjs` 按 `app.isPackaged` 分流 + 缺文件 fallback，详见 `electron/config.cjs`
- **关闭按钮语义**：Win/Linux/WSL 上 close 按钮 = hide to tray（window `setSkipTaskbar(true)`，程序继续在后台运行）；macOS 上 close 按钮 = hide window，Dock icon 保留可点击唤回（系统惯例）。三种重新唤起路径并存：菜单栏托盘菜单 / Dock icon 点击 / Cmd+Tab。统一由 `electron/tray.cjs::installCloseInterceptor` 实现，`bridgeDeps.isQuitting` flag 决定拦截还是放行
- **`isQuitting` 守卫**：作为 `bridgeDeps` 单例字段，所有退出路径（Cmd+Q / 托盘 Quit / 系统退出）统一在 `app.on('before-quit')` 中翻转为 `true`，`mainWindow.on('close')` 拦截器读到该 flag 才放行真退出，否则 `preventDefault` + `hide()`。`before-quit` 是 Electron 退出流程的唯一统一钩子，因此 flag 也只在此处设置
- **单实例锁**：`app.requestSingleInstanceLock()` 在 `app.whenReady()` 之前调用（平台常量定义后、`app.setName` 前），`second-instance` 事件由 `tray.cjs::registerSingleInstanceForwarder` 唤起现有窗口（必要时 `restore()` + `focus()` + `setSkipTaskbar(false)`）。获取锁失败调 `app.exit(0)`（用 exit 而非 quit 跳过 teardown，锁的拥有者负责清理）。dev opt-out 环境变量 `ZAST_DESKTOP_DISABLE_SINGLE_INSTANCE_LOCK=1` 跳过此锁，便于多实例调试
- **Windows AppUserModelID**：`'io.zast.agent'`（与 `package.json#build.appId` 对齐）通过 `app.setAppUserModelId` 在 `app.setName` 之后立即设置——Windows 通知分组、托盘关联、Action Center 显示都依赖此 ID，缺它所有 toast 会归属到 `electron.app.Zast` 而分裂
- **WSL 降级**：tray 创建失败（包括 WSL 的 Tray API 不可用、KDE/GNOME 缺 libappindicator 等）时 `rememberLog` 警告，降级为无托盘 + close 按钮 = 仅 hide window，程序仍保持后台运行（`backgroundThrottling: false` 已保证事件处理）

### ClientContext 数据流

Desktop login 时 main 进程必须把客户端本地 OS / skills 信息打包发给 Backend,否则 Backend 在云端容器里**完全无法**触达用户机器:

- **数据**:`environment_hints`（`platform release; arch=...; zast-desktop=...; node=...; zast_home=...`）+ `platform_hints`（User-Agent 形式）+ `skills: list[str]`（`$ZAST_HOME/skills` 下启用的 skill name 列表,desktop 已按 `config.yaml::skills.disabled` 过滤,并且额外按 `platforms:` frontmatter vs `process.platform` 过滤不兼容的 skill —— 渲染层的 Settings → Skills 也隐藏这些行,所以后端 prompt 块不会列出本机跑不起来的 skill）。
- **构造方**:`electron/client-context.cjs::buildClientContext`,仅 main 进程调用 — renderer 没有 `process.platform`,也读不到 `$ZAST_HOME/skills`。
- **注入点**:`electron/ipc/auth.cjs` 的 `zast:auth:login` handler 在 `session.login(payload)` 之前合并 `clientContext`(renderer payload 里的 `clientContext` 字段可覆盖,默认用 main 进程构造)。
- **传输**:`backend-session.cjs::login()` POST `/api/user/login` body 的 `client_context` 字段。
- **消费方**:`backend/routers/user.py` 写入 JWT `ctx` claim;`backend/core/system_prompt.py` 读 `client_ctx.skills` / `environment_hints` / `platform_hints`,缺失则 fallback 到 `PLATFORM_HINTS["webui"]` + 零 skills — 工具调用会基于错误环境执行。

不传或字段为 None 等于自降级到 webui 平台 + 无 skills;不是失败,是**静默退化**。

## 通信模型

### Renderer ↔ Main（IPC）

renderer 通过 `window.zastDesktop.*` 调 main，后者通过 `webContents.send(...)` 推事件。`preload.cjs` 是白名单。完整 IPC 命名空间列表见 [§IPC 命名空间](#ipc-命名空间) 表(19 个)——此处只提要点:chat WS **不走** preload 命名空间,直接由 renderer 的 `ZastGateway` 连 Backend。

### Renderer ↔ Backend（REST + WS）

- **REST**：renderer 经 `window.zastDesktop.api({ path, method, body })` 调 main → `connection.cjs` 转发到 Backend（携带 JWT）。401 时发 `zast:auth:session-expired` 给 renderer。**注意**：`ZastApiRequest` 不支持 `query` 字段——有查询参数时需直接内联到 `path`（如 `` `/api/insights/overview?days=${days}` ``）
- **WS**：`ZastGateway extends JsonRpcGatewayClient`，在 renderer 内**直接**连接 `ws://<backend>/api/chat/ws?token=<jwt>`。main 进程只通过 `zast:gateway:ws-url` 把 URL 推给 renderer，不在 IPC 层代理 chat 帧。因此**没有** `window.zastDesktop.chat.*` 命名空间。

### Main ↔ Runner（本地 WS）

`runner-bridge.cjs` 编排：
1. `start()` → 启动 WS server（127.0.0.1:0）→ 启动 Runner process（`--desktop-ws`）
2. Runner 连入 → `runner_ready` → bridge 调用 `get_tools` RPC
3. `get_tools` 结果缓存 → emit `running` 事件
4. Renderer 通过 `zast:runner:get-tools` IPC 拉到缓存的 Schema 后，调 `gateway.request('tools.sync', {tools})` 上报给 Backend（per-user registry）。`tool.call` / `tool.result` 是 chat turn 期间的工具调用/结果通道，**不**携带 schema 信息。

### 反向 RPC（Runner → Backend）

`runner-reverse-rpc.cjs`：处理 Runner 的 `request_llm` → 调 Backend `POST /api/llm/completion` → **透传 Backend 响应体**（proxy 不强制返回形状，由 Backend 端点契约保证）。守卫：max 200 messages、max 1MB payload。

## IPC 命名空间

| 模块 | 通道 | 说明 |
|------|------|------|
| `ipc/auth.cjs` | `zast:auth:login/logout/session/change-password` + `zast:model-config:get` | 登录/登出/会话恢复/修改密码；`model-config:get` 投影 `llm_*` 字段给 renderer（GCS 密钥在 IPC 边界剥离，recorder 通过 `session.getModelConfig()` 读取完整对象） |
| `ipc/connection.cjs` | `zast:connection`、`zast:gateway:ws-url`、`zast:boot-progress:get`、`zast:api` | **核心 REST 代理**：renderer 调 `api()` → main 转发到 Backend |
| `ipc/runner.cjs` | `zast:runner:status/start/stop/restart/invoke`、`zast:runner:invoke` | Runner 生命周期 + renderer 工具调用 |
| `ipc/terminal.cjs` | `zast:terminal:start/write/resize/dispose` + `zast:terminal:<id>:{data,exit}` | node-pty 终端 |
| `ipc/files.cjs` | `zast:readFileDataUrl`、`zast:readFileText`、`zast:selectPaths` | 文件读取 |
| `ipc/fs.cjs` | `zast:fs:readDir`、`zast:fs:gitRoot`、`zast:fs:gitBranch`、`zast:fs:completePath` | 目录操作 + git 分支 + @-路径补全（不依赖 `git` binary，直接读 `.git/HEAD`） |
| `ipc/clipboard.cjs` | `zast:writeClipboard`、`zast:saveClipboardImage` | 剪贴板 |
| `ipc/images.cjs` | `zast:saveImageFromUrl`、`zast:saveImageBuffer` | 图片保存 |
| `ipc/link-title.cjs` | `zast:fetchLinkTitle` | 链接标题获取 |
| `ipc/preview.cjs` | `zast:normalizePreviewTarget`、`zast:watchPreviewFile`、`zast:stopPreviewFileWatch` | 文件预览 |
| `ipc/runner-config.cjs` | `zast:runner-config:read/write` | 读写 `$ZAST_HOME/config.yaml`（atomic write + 校验 YAML 结构），写后 await `restartRunnerBridge()` |
| `ipc/skills.cjs` | `zast:skills:list`、`zast:skill:set-enabled`、`zast:toolsets:list`、`zast:toolset:set-enabled` | 列出 `$ZAST_HOME/skills` category + 切换 enabled；列出工具集 rosterset + 切换 enabled（写 `toolsets.disabled`，与 `skills.disabled` 同级 section）。两类启停写路径共享 `lib/config-writer.cjs` 的 in-flight 锁(避免 fast double-toggle 与 config write 竞争)。工具集 schema 来源是 `runnerBridge.getTools()` 缓存，filter 规则见 `lib/toolset-index.cjs` (`EXCLUDED_PREFIXES = ['mcp_']` 默认排除 MCP) |
| `ipc/settings.cjs` | `zast:setting:defaultProjectDir:{get,set,pick}` | 设置 |
| `ipc/system.cjs` | `zast:requestMicrophoneAccess`、`zast:notify`、`zast:version` | 系统 |
| `ipc/titlebar.cjs` | `zast:titlebar-theme` | 标题栏主题 |
| `ipc/external.cjs` | `zast:openExternal` | 外部链接 |
| `ipc/recorder.cjs` | `zast:recorder:startWithToolbar` / `pause` / `resume` / `stop` / `setData` / `finishUpload` / `hideToolbar` / `moveToolbar` + 出站 `recorder:upload-started` / `recorder:progress` / `recorder:finished` / `recorder:failed` | 屏幕录制（多模态 chat attachment 源）；录制完成后用 XHR POST 到 Backend `/api/media/recording/upload`（512MB cap、15min timeout、100ms 节流 `xhr.upload.onprogress` → `recorder:progress` IPC），Backend 用 boto3 S3-compatible 接口写 GCS。客户端不再持有 GCS HMAC secret |
| `ipc/update.cjs` | `zast:update:check` / `download` / `install` / `status` + 出站 `zast:update-event` | Electron 二进制自更新（`electron-updater`）。dev 模式全部 no-op |
| `ipc/media.cjs` | `zast:media:stt` / `zast:media:tts` | 后端 media 代理：multipart 上传音频给 `/api/media/stt`、下载 `/api/media/tts` 流并包成 base64 data URL 返回。`zast:api` 通用代理只支持 JSON，这两条走专用通道 |

完整 19 个 IPC 命名空间见 [§IPC 命名空间](#ipc-命名空间) 表格;每个模块 export `registerXxxIpc(deps)`,deps 由 `main.cjs` 显式注入。

`main.cjs` 内联 sender（4 个）:`zast:window-state-changed`、`zast:power-resume`、`zast:boot-progress`、`zast:close-preview-requested`。`zast:preview-file-changed` 在 `ipc/preview.cjs:122` 发出,**不** 计入 main.cjs inline sender 集合。

托盘 / 单实例锁 / 关闭按钮拦截由 `electron/tray.cjs` 独立管理（不在 `ipc/` 下），无需 IPC 通道——renderer 不感知窗口可见性变化（`backgroundThrottling: false` 已保证隐藏期间持续处理事件）。

### 本地 JSON-RPC 拦截（filesystem-bound calls）

`use-gateway-request.ts::tryLocalIntercept` 在 `requestGateway` 入口处拦截以下方法——这些方法依赖用户本地文件系统，后端在 Docker 中无法访问，必须在 desktop main 进程内直接处理：

| 方法 | 拦截逻辑 | IPC |
|------|----------|-----|
| `config.get({key:"project", cwd})` | 读 `.git/HEAD` 解析分支，返回 `{cwd, branch}` | `zast:fs:gitBranch` |
| `complete.path({word, cwd})` | 解析 `@` 后路径前缀，列目录项，目录优先、隐藏规则沿用 `FS_READDIR_HIDDEN` | `zast:fs:completePath` |

不在拦截表里的方法原样转发给后端 WS。`config.get` 的非-`project` key 仍然走后端（`UserSetting` 查表）。

### IPC 转发事件（mcp.reload）

后端 `dispatch_user_event` 发出的 JSON-RPC 事件 `mcp.reload` 携带 `call_id`。[src/app/session/hooks/use-message-stream.ts](src/app/session/hooks/use-message-stream.ts) 的事件分发器在收到该事件时：

1. 提取 `call_id` 与剩余参数（去掉 `call_id` 后转发 Runner）
2. 调 `window.zastDesktop.runnerDispatch('mcp.reload', args)` 转发给 Runner —— 这是第一类 JSON-RPC 方法（不走 `execute_tool`），经 `zast:runner:dispatch` IPC → `bridge.dispatch()` → `wsServer.call('mcp.reload', ...)` 直达 [runner/server.py `mcp.reload` 分支](../runner/server.py)
3. Runner 响应后通过 `gateway.request('tool.result', {call_id, result})` 回包

后端 IPC future 在 `tool.result` 抵达时 resolve，`reload.mcp` handler 同步返回 Runner 的 body 给 renderer。同一 IPC 通道复用 `tool.call` / `tool.result`，不引入新的 JSON-RPC 方法。

### Tool call args renderer join（live vs 历史一致）

Backend `tool.start` 在 wire 边界丢 args（[backend/core/jsonrpc_emitter.py:70-76](../backend/core/jsonrpc_emitter.py#L70-L76) 只透传 `tool_id`/`name`/`call_id`/`status`）；Runner-bound 工具（`terminal` / `execute_code` / `read_file` / `write_file` / `process` / `browser_*` / `skill_*` / `mcp_*`）的实际 args 走后续 `tool.call` 事件（`{name, args, call_id}`）。Renderer 在 [use-message-stream.ts:857-880](src/app/session/hooks/use-message-stream.ts#L857-L880) 的 `tool.call` 分支里 `upsertToolCall('running')` 把 args 注入 chat UI ToolPart（让 `dynamicTitle` 在 live 期间就能渲染 `Ran · <command>`）。`chat-messages.ts::toolId` 的查找链把 `payload.call_id` 作为 fallback，所以 tool.call 不需要额外把 call_id 映射成 `tool_id`/`tool_call_id`。原有的 IPC forward 保持不变。Backend-only 工具（`web_search` / `memory_*` 等）不发 `tool.call`，live 期间 args 仍为空，但 `dynamicTitle` 对它们已有 host/query 特化。历史会话走 `/api/sessions/{id}/messages` 的 OpenAI 风格 `tool_calls[]`，`toolPartFromStoredCall` 通过 `parseMaybeJsonObject` JSON.parse `function.arguments` 恢复 args —— 与 live 路径形状不同，但渲染走同一条 `dynamicTitle` 链路。

## Electron 二进制自更新(统一 desktop + runner)

Desktop 是预构建产物而不是从源码拉取的——更新走 `electron-updater`,从 Backend `/api/update` 拉取预构建安装包并原子替换。**一次更新同时刷新 desktop 二进制 + Python runner**(轮子 + `server.py`),保证两端不会因为版本错配而 broken。外层 Tauri `Zast-Setup` 负责 install / uninstall / repair。

**为什么必须两阶段**：invariant 是"任何阶段崩溃都不会留下 desktop-new + runner-old 的破损组合"。Phase 1 网络半途挂掉，旧 Electron 仍能正常工作（venv 未被动）；Phase 2 文件操作失败，`finally` 块回退到旧 runner。把"下载网络依赖"前置到 Phase 1 = "重启后断网也能完成 install"；venv 永远不被改名 / 移动 / 替换 = "Phase 2 出错可以原 venv 启动旧 runner"。两阶段合一则同时违反这两条。

数据流(两阶段,network 在前、file ops 在后,避免网络断在重启后变砖):

- **Phase 1 — prefetch (在 OLD Electron 里跑):** `main.cjs::setupAutoUpdater` 在 `app.whenReady` 后挂载,设置 `autoDownload = false`,延迟 30s 调 `autoUpdater.checkForUpdates()`。`autoUpdater.on('update-downloaded', info => runnerUpdater.prefetchRunnerAssets(...))` — 同时在后台从 `/api/update/latest-runner.yml` 下载轮子 + `server.py` 到 `$ZAST_HOME/runner.staging/`,验 RSA 签名 + SHA-512,通过后写 sentinel `.pending-runner-update.json`。manifest 拉取失败时短重试 3 次(1.5s/3s backoff),不引入未签名 fallback(`latest.yml` 的 `runner` block 仅作展示冗余,不参与 wheel 校验)。**渲染端的 "Restart now" 按钮只在 `runner-ready` 事件落地后才可点** — 永远不让用户重启到一个"desktop 已是新版但 runner 还没准备好"的破损态。
- **Phase 2 — install (在 NEW Electron 里跑):** `app.whenReady` 早期调 `runnerUpdater.installPending()`:读 sentinel → `runnerBridge.stop()` 释放 venv 上的 Python 句柄(关键!Windows 上有 EPERM 风险) → `pip install --upgrade "<wheel>"` 原地升级轮子到现有 venv(`pip` 的 `only-if-needed` 默认行为拉新 dep 的 diff;**不用 `--no-deps`**——那会禁止拉新 wheel 引入的新依赖,启动就 `ModuleNotFoundError`) → 覆盖 `server.py` → 冒烟 `import zast_agent, server` → `runnerBridge.start()` → 等 `runner_ready`。
- **venv 永远不被改名/移动/替换** — 只有 venv 内部的 wheel 被升级。任何失败都通过 `try / catch / finally` 在 `finally` 块里 `runnerBridge.start()` 拉起旧 runner;`attempt_count` 写回 sentinel,`>= max_attempts: 3` 时删 sentinel 并 emit `runner-failed {recoverable: false}` 让用户知道要重装。
- **Renderer 状态机:** `@/store/update` 同时持有 `$updateStatus`(desktop 7 态)+ `$runnerUpdateStatus`(runner 6 态);`@/main.tsx` 启动时挂两个 IPC 订阅。`shell/update-toast.tsx`(Radix Dialog)挂在 `desktop-controller.tsx` overlays 与 `<BootFailureOverlay />` 同级,状态机:desktop `available` → Download / `downloading` → 进度条 / `downloaded` → Restart now(等 `runner-ready`);runner progress 显示在 desktop 状态下方一行:prefetch (manifest / wheel / server) → ready → installing (pip / starting) → installed / failed。

发布流(运维):`scripts/build_client.ps1` 末尾调 `Build-UpdateZip` 产 `release/Zast-Setup-{ver}.exe`(首次安装)+ `release/Zast-{ver}-update.zip`(自更新,内含 desktop artifact + runner wheel + `server.py` + 4 个 `latest*.yml` + `manifest.json`)。Skills **不**打进 wheel,也不进 update zip —— skills 由 installer 在首装时从 `installer/payload/bundled-skills/` seed 到 `$ZAST_HOME/skills/` 与 `$ZAST_HOME/optional-skills/`,Runner 启动时通过 `sync_skills` 增量对账。发布通过 `/admin/` 的"版本管理"标签页上传 update zip;desktop 在运行时从 `desktop/config.json` 读后端地址,`main.cjs::setupAutoUpdater` 据此调 `setFeedURL`。Backend 把 `exe_*` / `mac_*` / `linux_*` / `runner_*` 四套字段写入 `update_versions` 表。

签名 keypair:私钥 `scripts/secrets/update.key` 在仓库内提交(简化部署/演示场景),`Build-UpdateZip` 用它签 `latest*.yml` 与 `latest-runner.yml`;公钥 `scripts/secrets/update.pub` 通过 `desktop/package.json#build.extraResources` 复制到 packaged desktop 的 `process.resourcesPath/update.pub`,`runner-updater.cjs` 启动时读取并用它校验 `latest-runner.yml` 的 RSA 签名。`desktop/package.json#build.publish.url` 现在是 `https://resolved-at-runtime.invalid/api/update` —— electron-builder 仍会把它写入 `app-update.yml`(保持 channel / `useMultipleRangeRequest` / `updaterCacheDirName` 等元数据),但实际 fetch URL 始终来自 `setFeedURL`,不要直接依赖 `app-update.yml` 的 URL 字段。

## 行为契约

登录流程 / 工具调用中转 / Runner 自杀机制见 [design.md §3.3](../design.md)（工具调用完整链路）与 [design.md §4](../design.md)（认证与安全模型）。

`main.cjs` 顶层构造 `bridgeDeps` 对象，承载 `backendSession` + `runnerBridge` 两个跨模块单例。`ipc/auth.cjs` 写 `deps.backendSession`，`ipc/runner.cjs` 写 `deps.runnerBridge`。

Settings → Skills 是内嵌双 Tab 子页面 (`SkillsToolsTabs`)，URL `?tab=skills` 命中后由 `useRouteEnumParam('subtab', ['skills','toolsets'], 'skills')` 决定渲染哪一面；状态切换不增 history 项(replace)。命令面板的 `cc.nav.skills.title`(`'技能与工具' / 'Skills & Tools'`)指向 `/settings?tab=skills`。

### Settings → Skills 平台过滤

每个 SKILL.md 在 frontmatter 声明 `platforms: [macos|windows|linux]`(或留空表示跨平台)。Main 进程 `electron/lib/skill-index.cjs::listSkillsFromDisk` 在解析时根据 `process.platform` 给每条记录盖一个 `compatible: boolean`,并在 `buildSkillSummaries` 一并序列化给 renderer。Renderer `skills-settings.tsx` 直接过滤 `!compatible` 的行 —— 用户既看不到那一行也不能开关。如果所有 skill 都被隐藏（当前平台 Zast 编译产物内的 skill 一个都不支持），显示专门的 "No skills available on this platform" empty state。`ipc/skills.cjs::zast:skill:set-enabled` 在 main 端额外拒收不兼容的 enable 请求（防御性，旧版 renderer / 程序化调用无法绕过）；`electron/client-context.cjs::buildClientContext` 同样按 `compatible` 过滤 `client_context.skills`,Runner 的 `skill_matches_platform` 做运行期兜底。设置面板的 inline 提示在 `Settings → Account → Web Search` — 当 `extract_backend='tavily'` 但未配置 Tavily key 等场景显示 `InlineNotice`,数据来自 `ZastConfigResponse.web.*_set`,无需新增 IPC。

### 子代理会话可见性

侧栏 recents 默认隐藏子代理会话（与 backend `/api/sessions?include_subagents=false` 对齐）—— 子代理是父 turn 的派生物，自动生成的 "Subagent Task" 标题没有信息量。开关存在 Settings → Account → Agent Defaults 末尾（`display.show_subagents_in_sidebar`），持久化走现有 `UserSetting` flat key + `_flatten()` round-trip，无 schema 迁移。

可达性安全网（开关无论 on/off 都生效）：

- **搜索**：`/api/sessions/search` 不受 `include_subagents` 影响——子代理始终可搜。
- **直接 URL**：`GET /api/sessions/{id}/messages` 等单会话端点不受过滤影响。
- **状态栏入口**：底部状态栏 `SubagentsPopover`（[desktop/src/app/shell/subagents-popover.tsx](src/app/shell/subagents-popover.tsx)）作为右栏 item `id: 'subagents'`，点击展开当前 active session 的子代理列表（数据来自 `desktop/src/store/subagents.ts::upsertSubagent`，每行带 `sessionId`）。`sessionId` 非空的行可点击，调 `use-session-actions.ts::resumeSession` 路由到 `/chat/<subagentSessionId>`；`sessionId` 为空的是 `delegate_task` 兜底行（id 前缀 `delegate-tool:`），渲染为禁用态。

流转：`use-zast-config.ts::refreshZastConfig` 把 `config.display.show_subagents_in_sidebar` 写到 `$showSubagentsInSidebar` atom；`desktop-controller.tsx::refreshSessions` 通过 `$showSubagentsInSidebar.get()` 同步读最新值，拼到 `listSessions(limit, 1, 'exclude', 'recent', includeSubagents)`。`mergeSessionPage` 的 keep-set 行为不受影响——pinned / working / active 子代理即便在 default 隐藏模式下仍会保留，不会被过滤误杀。Settings 保存后通过 `onConfigSaved={() => refreshZastConfig().then(refreshSessions)}` 链式调用，自动重拉侧栏。

## 安全

### Token 加密存储

JWT 经 `safeStorage` 加密后落盘，userData 目录权限由 OS 控制。

### Model Config IPC 安全边界

`zast:model-config:get` IPC handler 在返回 renderer 前投影仅 `llm_*` 字段，剥离 `gcs_access_key` / `gcs_secret_key` / `gcs_bucket_name`。recorder 通过 `session.getModelConfig()` 直接读取完整对象（含 GCS 凭据），两条路径共享 5min 内存 cache。

### 路径白名单

`hardening.cjs` 的 `resolveReadableFileForIpc` 拒绝路径遍历（`../`）、符号链接逃逸、超大读取、敏感文件（.env/.ssh/.pem 等）。

### Runner 进程隔离

Runner 作为独立子进程，所有工具调用经本地 WS 中转。

## 端点文件与 Runner 重连

Desktop WS server 启动后将 `{port, pid, timestamp}` 写入 `$ZAST_HOME/desktop-endpoint.json`（atomic write + rename），stop 时清理。Runner 在 WS 断开后进入重连循环（指数退避 2s → 30s，最多 15 次约 5 分钟），每次重试前读取端点文件获取最新端口，并检查 Desktop PID 是否存活以跳过残留文件。

## 主动 Token 刷新

`backend-session.cjs` 在 login/restore 后调度定时器，于 `tokenExpiresAt - 5min` 自动调 `refresh()` 获取新 token。刷新失败不触发 logout——token 自然过期后由 WS close 1008 或 REST 401 触发 session-expired 流程。

## Gateway 重连退避

`reconnect.ts` 的退避公式带 0~50% 随机 jitter（`base + Math.random() * base * 0.5`），防止服务端闪断恢复时 thundering herd。

## 工具调用快速失败

`use-message-stream.ts` 收到 `tool.call` / `mcp.reload` 时检查 `$runnerOnline` 状态，Runner 不在线时立即通过 `tool.result` 回传错误给 Backend。Backend 侧的对称快速失败在 `_dispatch_runner_tool` ([backend/CLAUDE.md §快速失败](../backend/CLAUDE.md))—— Desktop 在线 + Runner tools 已注册的双重门控，在 `emitter.send_json` 抛 `WebSocketDisconnect`/`RuntimeError` 时也落入同一条快速失败路径。

## WS Close Code 感知重连

`JsonRpcGatewayClient` 暴露 `lastCloseCode` 属性。`use-gateway-boot.ts` 在 `onState('closed')` 时检查 close code：1008（Backend auth 失败）直接触发 `logout()` 跳登录页，不走退避重连。

## 已知限制

| 限制 | 说明 |
|------|------|
| Runner 崩溃后重连窗口有限 | 端点文件 + 重连循环（~5 分钟），超时后 Runner 退出 |
| Electron 42 + pnpm 11 需 hoisted | 失去 phantom-deps 防护；等 Electron ESM 主进程支持 |
| `.cjs` + `.ts` 双 runtime | 新增 main 模块用 `.cjs`，renderer 用 `.ts/.tsx`；等 Electron ESM 主进程支持 |
| WSL 下无系统托盘 | Electron Tray API 在 WSL 不可用；降级为关闭按钮 = 仅 hide window，无托盘菜单；**WSL 下无显式退出入口**，只能通过 `kill <pid>` 退出 |
| Linux 桌面环境差异 | KDE/GNOME 对 tray 支持不一；部分发行版需要 libappindicator 扩展；若 `new Tray()` 抛错，降级为 hide-only 模式 |
| 单实例锁 dev opt-out | `ZAST_DESKTOP_DISABLE_SINGLE_INSTANCE_LOCK=1` 环境变量可跳过单实例锁，便于多实例调试；正常用户无需关心 |
