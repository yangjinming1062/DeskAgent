# DeskAgent 跨模块协议契约

> 本文档收纳 Backend ↔ Client ↔ Runner 之间**所有跨模块共享的协议契约**——任何模块边界上"改了这里必须同步改那里"的硬约束。架构动机（为什么这么设计）见 [ARCHITECTURE.md](ARCHITECTURE.md)；产品设计意图（这套契约为什么长这样）见 [DESIGN.md](DESIGN.md)；具体实现、文件路径、错误码、配置项见各模块 `README.md`。

## 0. 协议栈总览

全链路采用 **JSON-RPC 2.0** 封装双向流量。三类链路共用同一信封：

| 链路 | 方向 | 传输 | 鉴权 | 见 |
|------|------|------|------|----|
| **Backend ↔ Client** | 双向 | WebSocket 长连接 (`/api/chat/ws?token=<jwt>`) | 用户 JWT（query string） | §1 |
| **Client ↔ Runner** | 双向 | 本地环回 WebSocket (`ws://127.0.0.1:<port>/rpc`) | 127.0.0.1 限制 + 动态端口 | §2 |
| **Runner → Client (反向 RPC)** | Runner → Client → Backend | 嵌套在 §2 上,经 Client 转发到 `/api/llm/completion` | Client JWT | §3 |

**信封格式**:
- 请求: `{"jsonrpc": "2.0", "id": "<call_id>", "method": "<method>", "params": {...}}`
- 事件: `{"jsonrpc": "2.0", "method": "event", "params": {"type": "<event_type>", "payload": {...}}}`
- 响应: `{"jsonrpc": "2.0", "id": "<call_id>", "result": ...}` 或 `{"jsonrpc": "2.0", "id": "<call_id>", "error": {"code": -32xxx, "message": "..."}}`

**核心约定**:
- `call_id` 是整张表的**唯一 Future Key**——Backend IPC future 字典按 `(user_id, call_id)` 寻址,跨用户不共享。
- 事件（notification）无 `id`,不可被响应;请求与响应必须按 `id` 配对。
- WS 关闭码 `1008`（鉴权失效）= 立即退出重连流程,不再尝试。

---

## 1. Backend ↔ Client 协议

### 1.1 JSON-RPC 方法（伙伴层扩展）

普通 chat / tool 类方法见 [backend/README.md](backend/README.md)。以下是**伙伴生命周期**专用的方法,Client 必须实现消费状态机（详见 [DESIGN.md §5–§6](DESIGN.md)）。

| 方向 | 方法 | 用途 | 关键约束 |
|------|------|------|----------|
| Client → Backend | `onboarding.get_state` | 查询已采集字段 + 下一个未答问题（断点恢复） | 返回 `is_complete=True` 仅在 `Persona.is_complete` + `voice` + `user_*` 全部齐后才置位;`next_field` 优先级 **voice 先于 user_***（音色子阶段排在用户子阶段之前，见 DESIGN §5.2） |
| Client → Backend | `onboarding.submit` `{field, value}` | 逐字段增量持久化 onboarding 答案 | 答案按子阶段分流——角色子阶段（含 `speaking_style`）触发 `PUT /api/companion/persona`;finalize 后只接受 `voice`（落 draft）与 `user_*`（upsert 到 `Memory` 表） |
| Client → Backend | `avatar.regenerate` `{feedback?}` | 重生 portrait 头像（不重跑全身） | 不触发 3D 模型失效 |
| Client → Backend | `avatar.generate_fullbody` `{avatar_id}` | 生成正/右/背三视图种子图 | 与 `avatar.regenerate` 共用 per-user 锁;并发返回 `already_running` |
| Client → Backend | `tts.match_voice` `{preference}` | 描述句 → voice id（标签评分） | 主流程 |
| Client → Backend | `tts.design_voice` `{prompt, preview_text?}` | LLM 生成专属音色 | 增强路径,不走主流程 |
| Client → Backend | `tts.list_voices` | 枚举目录 | 工具窗口不 boot WS 网关,改走 REST `GET /api/companion/voices` |
| Client → Backend | `companion.set_disturbance_tier` `{tier}` | 上报 effective 档位（积极主动/常规/保持安静） | Client 是档位唯一权威,Backend 仅镜像;30s 轮询 + 变化即推 |
| Client → Backend | `companion.check_affect` `{idle_seconds, local_hour}` | idle 触发的情境化 affect 推理 | Backend 加载 persona + 记忆跑一次 LLM,决定是否 emit `companion.affect` |
| Client → Backend | `companion.interact` `{kind: 'poke'\|'drag', tone, poke_count, idle_seconds, local_hour}` | 单次戳/拖的 LLM 反应推理 | per-user inflight 取消 + 1.5s 节流 + Client 端 2s debounce;RPC 失败静默吞掉,本地池兜底 |
| Client → Backend | `companion.record_interaction_stats` `{kind: 'poke'\|'drag'\|'chat_turn', hour}` | 互动统计上报 | 无 LLM;三类计数各自 ≥ 10（双门限）才 upsert `Memory(context="interaction_stats:<date>")` |
| Client → Backend | `companion.get_user_profile` | 拉取 `Memory(context="user_profile:*")` 5 条结构化字段 | persona-retune wizard 第 5 步预填用 |
| Client → Backend | `GET /api/companion/model` | 查询当前 3D 模型状态 | `species` / `provider` / `asset_url` |
| Client → Backend | `POST /api/companion/model` | 触发 3D 模型异步生成 | 全身三视图 → Tripo3D multiview-to-3D + rig;进度经事件推送 |
| Client → Backend | `POST /api/companion/avatar` / `/from-image` | 头像半身生成（步 1） | 同步;失败返回 502 + 友好文案,不暴露 provider 原始错误 |
| Client → Backend | `POST /api/companion/avatar/{avatar_id}/fullbody` | 三视图种子图生成（步 2） | 同步;头像不存在 404;并发 429 |

### 1.2 事件类型

| 事件 `type` | 触发时机 | 关键 payload 字段 | 消费者 |
|------------|----------|-------------------|--------|
| `companion.affect` | 非言语的情境化情绪反应 | `{emotion, locale?, target?}` | EMOTIONAL 状态切换;quiet 档也透传（断消息不断 affect） |
| `avatar.regenerated` | `avatar.regenerate` 最终结果 | `{job_id, asset_url?, seed_front_url?, seed_right_url?, seed_back_url?, id?, error?}` | 替换头像或展示失败提示 |
| `avatar.fullbody_generated` | `avatar.generate_fullbody` 完成 | 同上 seed_* 字段 | 替换三视图 |
| `model.ready` | 3D 模型异步生成就绪 | `{model_id, asset_url, species}` | 客户端加载 GLB + 注入 TS 动画 clip + 状态机绑定 |
| `model.gen.progress` | 模型生成中 | `{progress: 0..1, stage}` | 可选进度展示 |
| `model.failed` | 模型生成失败 | `{error}` | 渲染程序化蛋形兜底角色（无气泡、无错误） |
| `wardrobe.updated` | 换装产物就绪 | `{texture_url, palette}` | 热替材质/纹理（不动骨骼动画与 morph） |
| `video_gen.completed` | 视频生成成功 | `{task_id, url}` | 媒体展示 |
| `video_gen.failed` | 视频生成失败 | `{task_id, error}` | 用户可见错误 |
| `reload.mcp` | MCP 配置变更后服务器主动通知 | `{}` | Client 转发给 Runner,Runner 重新加载,再 `tools.sync` 回 backend |

### 1.3 Affect 契约

**语义/渲染解耦**——Backend 只产出 emotion + 可选 locale 语义,绝不指定渲染方式或像素坐标。

**emotion 枚举**（17 项,`services/chat/affect.py::ALLOWED_EMOTIONS` 权威源）:

```
happy / sad / surprised / excited / confused / concerned / shy / proud /
grateful / playful / bored / lonely / sleepy / curious / embarrassed /
apologetic / neutral
```

**locale 枚举**（`services/chat/affect.py::ALLOWED_LOCALES` 权威源）:

```
home / chat / perch / roam / sleep
```

**spatial target**（可选,仅 `perch` 时有意义）:窗口/进程名关键字,CJK/空格/大小写允许。Client 经 `system.get_windows` 解析为窗口几何后计算 perch 点。

**inline 空间 cue 规则**:LLM 在回复前自填 `[spatial:LOCALE,target:KEYWORD]`,由 `AffectScrubber` 解析后附加到 `message.complete.affect.locale/target`。Backend 解析后下发,Client 决定是否落位（§3 档位门控 + chat-open 抑制）。

**扩展协议**:每次扩展 emotion / locale 须同步更新 Backend allowlist + 客户端 morph target 目录,未覆盖项一律按 `neutral` / `home` 处理。

### 1.4 资产 URL 签名

| 资产 | 路径前缀 | TTL |
|------|----------|-----|
| portrait 头像/种子图 | `companion-avatars/` | `signed_url_expiry_seconds=300`（5 分钟） |
| 3D 模型 GLB | `companion-models/` | 5 分钟 |
| 换装产物（纹理） | `companion-assets/` | 5 分钟 |

**约束**:URL HMAC 签名（具体算法见实现）;换设备登录需重新生成签名,不能直接分享原 URL;Client 收到后应本地缓存避免重复拉取。

### 1.5 错误信封

REST 端点异常路径返回统一结构:

```json
{ "error": "<short_code>", "reason": "<FailoverReason|None>", "status": <http_status> }
```

WS JSON-RPC 错误使用标准 JSON-RPC 2.0 错误码（`-32700` 到 `-32603`）。**关键契约**:
- `-32603`（内部错误）抛至前端前必须脱敏,严禁包含数据库账号、服务器本地路径等栈帧细节。
- 21 种 `FailoverReason` 见 [backend/README.md §错误分类管道](backend/README.md),决定恢复策略（退避重试 / 凭证轮换 / 压缩上下文 / 不重试）。
- 流式 chat 一旦首个 chunk 已发出,任何 provider 失败**不再 fallback**——用户已看到部分输出,切换 provider 只会造成 transcript 截断。

---

## 2. Client ↔ Runner 协议

### 2.1 链路特性

- Runner **主动**连 Client 提供的 WS（`ws://127.0.0.1:<port>/rpc`）,启动参数 `--desktop-ws`。
- 启动后发 `runner_ready` 握手通知,Client 据此在握手阶段决定是否暴露语音通话 / 唤醒词 / 主动陪伴等依赖 OS 能力的功能。
- 端口是**动态高位端口**,由 Client 主进程分配并注入 Runner 启动参数。
- 限定 `127.0.0.1`——不接受外部接口连入。

### 2.2 RPC 方法清单

| 方法 | 方向 | 用途 | 见 |
|------|------|------|-----|
| `runner_ready` | Runner → Client | 启动握手通知;携带 `version` + `capabilities` | §2.3 |
| `tools_changed` | Runner → Client | 工具 schema 变更（MCP 后台发现完成）;Client 重拉 `get_tools` 再 `tools.sync` 到 backend | — |
| `get_tools` | Client → Runner | 获取工具 schema（已过滤：toolset disabled & capability check 失败的不返回） | — |
| `deskagent.info` | Client → Runner | 完整运行快照（version / uptime / capabilities / system / tool_count / mcp_servers / network_reachable / disk_free_bytes） | — |
| `execute_tool` | Client → Runner | 执行工具调用,返回 `{ok, result|None, error?}` | — |
| `mcp.reload` | Client → Runner | 重新加载 MCP server 配置 | — |
| `request_llm` | Runner → Client | 反向 RPC——借大脑（见 §3） | §3 |

### 2.3 `runner_ready` payload

```json
{
  "version": "0.2.0",
  "capabilities": {
    "microphone": false,
    "screen_capture": true,
    "local_stt": false,
    "local_tts": false,
    "system_activity": true,
    "platform": "win32",
    "python": "3.11.13"
  },
  "probe_failed": false
}
```

**契约**:
- `capabilities.local_stt` / `local_tts` 等由运行时探测（实际枚举设备、调用底层 API）,**不**退化为 `import` 是否存在——后者欺骗 UI 让用户点不能用按钮。
- `probe_failed == true` 表示 capability 探测整体抛异常,Client 应把这条 handshake 视为"功能状态不可信"。
- 语音通话 / 唤醒词在 capability 为 `false` 时被 Client **静默隐藏**,伙伴不强提示。

---

## 3. 反向 RPC 桥接（Runner 借大脑）

**核心约束**:
- Runner **零凭证运行**,不持有任何 Backend Token;所有出站 LLM 请求必须向上借道 Client。
- 速率守卫：Client 转发前统计单会话请求次数与载荷大小（硬上限 200 帧 / 1MB）,防止 Runner 工具逻辑失控刷爆 LLM 额度。

**链路**:

```
Runner ──(Local WS: method="request_llm")──> Client ──(HTTP POST: /api/llm/completion)──> Backend ──> LLM
                                                │
                                        (Client JWT 鉴权)
```

---

## 4. IPC Future 桥接（Backend 侧契约）

`call_id` 是 Backend IPC future 字典的**唯一 Future Key** 标识生命周期。

**键结构**: `_pending: dict[(user_id, call_id), Future]` ——键是 `(user_id, call_id)` 二元组,而非单 `call_id`:
- 并发用户不共享 future;
- `user_id` 来自 JWT 解析,不是从工具入参中拿（reserved key 保护）；
- WS 断开时 `discard_user` 取消该用户**所有**未决 future。

**超时**: `ipc_future_timeout_seconds`（默认 300s）,超时返回 synthetic error。

**快速失败**: `_dispatch_runner_tool` 做 active_connections → has_runner_tools → send_json 异常三层检查,通常 < 100ms 返回离线错误;仅绕过三层后才进入 300s 超时。

**JWT 过期边界**: token 在 IPC 飞行途中过期时 Client 的 `tool.result` 被 `gateway/auth` 拒绝 → future 挂起直到超时。`discard_user` 在 WS 断开时清理,但 token 过期不触发 WS 断开——当前靠超时兜底。

**通用事件分发**: `dispatch_user_event(user_id, event_type, payload)` 复用同一 future 通道发任意 JSON-RPC 事件（如 `reload.mcp`）。

---

## 5. 跨模块安全契约

### 5.1 Reserved Keys（防 LLM 入参注入）

LLM 工具入参**禁止**覆盖以下保留键（Backend `registry.execute_backend_tool` 静默丢弃）:

```
user_id / llm_config / user_settings
```

**角色定义同等保护**: 角色定义作为系统提示词的一部分,同样受此保护,防止用户对话内容注入改写伙伴人格。

### 5.2 不可信工具结果包裹

外部（Web 搜索 / 浏览器抓取 / MCP 外部资源）获取的字符串注入 LLM 上下文前强制用 `<untrusted_tool_result>` 标签包裹。短字符串（`untrusted_wrap_min_chars`,默认 32）不包——注入风险低 + 节省 token。

### 5.3 凭据落盘

JWT 经 Electron `safeStorage` 加密落盘（`agent-session.json`）:
- Windows: DPAPI
- macOS: Keychain
- Linux: libsecret（但 Runner/Desktop 不支持 Linux,仅作原理说明）

Renderer 与 Preload 进程**不可访问** safeStorage 接口,阻断 XSS 窃取凭证。

### 5.4 API Key Fingerprinting

`GET /api/user/model-config` 只返回:
- 用户在 `UserModelConfig` 行里**显式设置**的字段；未设置的字段返回空字符串,**绝不**透传 `SETTINGS`（`.env`）里的服务器默认值。
- 原始 API key 永不离开后端,仅返回 `*_api_key_set: bool` + `llm_api_key_fingerprint` 指纹（`sk-…XX` 形式）。

`PUT /api/user/model-config` 允许用户自助修改:空 `api_key` = 保留原值;空 `base_url`/`model_name` = 清空回退默认。

### 5.5 Outbox 与 WS 事件下发

主动陪伴消息、Cron 定时、形象/角色变更通知等所有"伙伴主动行为"经 PostgreSQL LISTEN/NOTIFY + `ws_events` outbox 表下发:

1. 业务或 Cron 协程将待发送 WS 帧写入 `ws_events` 表。
2. PostgreSQL STATEMENT 级触发器在 INSERT 时自动发出 `NOTIFY ws_events_channel`。
3. 副本认领（Atomic Claim）:每个 Backend 副本独立 `LISTEN`,收到唤醒后执行 `DELETE ... RETURNING`;行锁保证仅一台副本原子获取并消费该行。
4. 调度器 tick 只写入事件不 await WS 发射,避免慢客户端拖垮事务。

**单实例语义**: `disturbance_tier` 与 IPC future 由 process-local 状态承载,架构**不支持**多实例水平扩展。

### 5.6 自更新签名（Client ↔ Backend / Installer ↔ Backend）

| 通道 | 校验 | 备注 |
|------|------|------|
| Electron 二进制自更新 | `electron-updater` RSA | 走 `electron-updater` 通道 |
| Runner wheel 自更新 | SHA-512 + `scripts/secrets/update.pub` RSA 双重校验 | 强校验公钥签名,签名不匹配在 Staging 阶段直接拦截 |
| Skills 走单独通道 | 由 installer 首装 seed,client 自更新不下载 | 见 [installer/README.md](installer/README.md) |

**两阶段更新契约**（避免升级中途断网/崩溃变砖）:
- Stage 1（Prefetch）:下载新版 Electron + Runner wheel 到 staging 区,强校验签名 + SHA-512,写 Sentinel。
- Stage 2（Install,用户点 Restart & Install 后）:释放旧 Runner 句柄,`pip install --upgrade` 原地覆盖,Python 导入冒烟测试,失败回滚到旧版。

**核心约束**: Runner venv 目录**永不**重命名或移动,确保任意升级阶段崩溃时旧版 Runner 依赖树仍完全可用。

---

## 6. ID 语义

| ID 类型 | 格式 | 生命周期 | 唯一性范围 |
|--------|------|----------|------------|
| `conversation_id` | 整型 | 单次会话 | 全局唯一,DB 主键 |
| `session_id` | 字符串 | WS 连接周期 | 单 (user_id, conversation_id) 内唯一 |
| `call_id` | 字符串 | 单次 RPC 调用 | **整张表唯一**（用作 Future Key） |
| `task_id`（视频生成） | 字符串 | 异步任务周期 | 单 (user_id, provider) 内唯一 |

**职责分立**: 通信协议边界必须完成整型 `conversation_id` 与字符串 `session_id` 的显式转换;`call_id` 作为唯一 Future Key 标识生命周期（见 §4）。

---

## 7. 跨模块语言规则

LLM 在生成 `affect` / `spatial` 时按以下规则:
- `affect: {emotion}` 中的 emotion 必须从 §1.3 枚举集选,LLM 在回复前自填 `[affect:EMOTION]` 由 `AffectScrubber` 解析。
- `[spatial:LOCALE,target:KEYWORD]` 中 LOCALE 从 §1.3 locale 枚举选,target 可选,仅 `perch` 时有意义。
- **Backend 不产出像素坐标** —— Client 据 locale + 当前空间状态决定最终位置与 locomotion;`target` 仅在 `perch` 时由 Client 经 `system.get_windows` 解析为窗口几何后计算 perch 点。

---

## 8. 维护规约

- 本文档是**跨模块公共契约**——任何改动必须同时通知所有受影响的模块所有者。
- 任何扩展 emotion / locale / 事件 type,必须在本文档 + Backend allowlist + Client 消费代码**三处同步**。
- 任何 Reserved Key 新增,必须在本文档 + `registry.execute_backend_tool` 同步。
- 子模块 README 不重复本文档内容,只在需要时链接。