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

### 1.0 WS vs REST 命令路由原则

Backend ↔ Client 同时暴露 JSON-RPC over WebSocket（`/api/chat/ws`）与 HTTP REST（`/api/...`）。两套通道的**设计意图不同**——选错通道等于把一类语义错配到不属于它的链路。

| | WS（JSON-RPC） | REST |
|---|---|---|
| **设计意图** | 「绑定 process-local 上下文的 push-only 持续通道」——事件、进度与小包数据流以 WS 为单一推送路径,关 WS 即随之丢弃运行时状态。 | 「URL 寻址、无状态 CRUD」——任何持有 JWT 的入口（Hub、CLI、脚本、第三方集成）可独立调用,与 chat 是否在线无关。 |
| **承载语义** | 长会话、跨多次往返、需要 process-local 锚点的小包 | 幂等 CRUD、可独立寻址的对象、多 KB-MB 载荷上传 / 下载 |

**判别启发（顺序问）**：

1. **依赖 process-local 状态吗**？（运行时会话、IPC future、per-user 锁、`disturbance_tier` 镜像） → 是 → **WS**。
2. **需要在 chat 未连接时执行吗**？（被另一窗口 / CLI / 脚本调用） → 是 → **REST**。
3. **是生产者 / 进度 / 状态推送**？通知侧**永远经 WS outbox + 事件帧下发**（与命令端走哪条无关）。

**REST 镜像特例**:某条 WS 方法的读被一个**不持有 WS 连接**的 UI 表面（典型为 Hub）需要时,可保留 REST 镜像。镜像必须包装**同一个服务函数**,不允许分裂第二条代码路径;两端契约必须等价,任何漂移都要双端同步。

### 1.1 JSON-RPC 方法（伙伴层扩展）

普通 chat / tool 类方法见 [backend/README.md](backend/README.md)。以下是**伙伴生命周期**专用的方法,Client 必须实现消费状态机（详见 [DESIGN.md §5–§6](DESIGN.md)）。

| 方向 | 方法 | 用途 | 关键约束 |
|------|------|------|----------|
| Client → Backend | `onboarding.get_state` | 查询已采集字段 + 下一个未答问题（断点恢复） | 返回 `is_complete=True` 仅在 `Persona.is_complete` + `is_portrait_confirmed` + `voice` + `user_*` 全部齐后才置位;`next_field` 在形象未确认时根据种子图生成阶段返回 `portrait`、`portrait-fullbody-front`、`portrait-fullbody-right` 或 `portrait-fullbody-back`（单视图模式下正面全身完成后即返回 `portrait-fullbody-front`,不路由 right/back）,确认后按 **voice 先于 user_*** 路由;响应含 `fullbody_mode`（`"single"` / `"multi"`）供客户端决定是否展示侧面/背面阶段;响应含 `default_fullbody_reference_source`（`"avatar"` / `"reference_image"`，preset species 默认 `"avatar"`，非 preset 默认 `"reference_image"`）供客户端设置全身图参考来源的默认值 |
| Client → Backend | `onboarding.submit` `{field, value}` | 逐字段增量持久化 onboarding 答案 | 答案按子阶段分流——角色子阶段（含 `speaking_style`）触发 `PUT /api/companion/persona`;finalize 后只接受 `voice`（落 draft）与 `user_*`（upsert 到 `Memory` 表） |
| Client → Backend | `avatar.regenerate` `{feedback?}` | 重生 portrait 头像（不重跑全身） | 不触发 3D 模型失效 |
| Client → Backend | `tts.match_voice` `{preference}` | 描述句 → voice id（标签评分） | 主流程 |
| Client → Backend | `tts.design_voice` `{prompt, preview_text?}` | LLM 生成专属音色 | 增强路径,不走主流程 |
| Client → Backend | `tts.list_voices` | 枚举目录 | 工具窗口不 boot WS 网关,改走 REST `GET /api/companion/voices` |
| Client → Backend | `companion.set_disturbance_tier` `{tier}` | 上报 effective 档位（积极主动/常规/保持安静） | Client 是档位唯一权威,Backend 仅镜像;30s 轮询 + 变化即推 |
| Client → Backend | `companion.check_affect` `{idle_seconds, local_hour}` | idle 触发的情境化 affect 推理 | Backend 加载 persona + 记忆跑一次 LLM,决定是否 emit `companion.affect` |
| Client → Backend | `companion.interact` `{kind: 'poke'|'drag', poke_count, idle_seconds, local_hour}` | 单次戳/拖的 LLM 反应推理 | 1.5s 节流 + per-(user,kind) inflight 去重 + 5min 成本封顶（用户主动触发专属，客户端/服务端双侧校验，不压制统计上报）；封顶窗口仅在成功产出反应时消耗，失败/超时/inflight/rate_limited 不消耗；RPC 失败静默回退本地池 |
| Client → Backend | `companion.should_act` `{kind, idle_seconds, local_hour, focused_category, fullscreen, screen_locked, seconds_since_last_action}` | LLM 反驱动的自主空间行为决策 | 2.0s 节流；客户端事件驱动 + 30min 兜底；should_act: false 为合法响应且必须尊重；失败时不擅自补决策 |
| Client → Backend | `companion.record_interaction_stats` `{kind: 'poke'|'drag'|'chat_turn', hour}` | 互动统计上报 | 无 LLM；任一 kind ≥ 10（OR 门限）即 upsert `Memory(context="interaction_stats:<date>")` 带 hour_counts 快照 |
| Client → Backend | `companion.get_user_profile` | 拉取 `Memory(context="user_profile:*")` 5 条结构化字段 | persona-retune wizard 第 5 步预填用 |
| Client → Backend | `POST /api/companion/portrait/confirm` | 确认形象（半身/全身） | 幂等;设置 `is_portrait_confirmed=True`,解开 voice/user_* 子阶段 |
| Client → Backend | `GET /api/companion/model` | 查询当前 3D 模型状态 | `species` / `provider` / `asset_url` |
| Client → Backend | `POST /api/companion/model` `{species_override?, provider?}` | 触发 3D 模型异步生成 | `fullbody_mode="single"`: 正面单图 → Tripo3D image-to-model + rig;`fullbody_mode="multi"`: 全身三视图 → Tripo3D multiview-to-3D + rig;**或** Blender+LLM 回退管线（见 §1.5）;进度经事件推送;`provider` 默认 `None` (auto-detect),可选 `"tripo"` 显式锁 Tripo 或 `"blender_llm"` 显式锁 Blender 管线 |
| Client → Backend | `POST /api/companion/avatar` / `/from-image` | 头像半身生成（步 1） | 同步;失败返回 502 + 友好文案,不暴露 provider 原始错误 |
| Client → Backend | `POST /api/companion/avatar/{avatar_id}/fullbody` | 链式参考生成全身种子图（步 2） | 同步;缺少正面全身 409;头像不存在 404;并发 429;`stage` 与 `view` 互斥必选其一;单视图模式（`fullbody_mode="single"`）下 `stage="aux"` 和 `view="right"/"back"` 被拒绝;可选 `reference_source`（`"avatar"` / `"reference_image"`，默认 `"avatar"`）+ `reference_image`（base64）+ `reference_content_type`：`"reference_image"` 时正面全身图以用户原始参考图为主参考（保留身材/体态），头像自动作为 secondary reference 供 Gemini 双参考融合美化风格;`reference_source="reference_image"` 时 `reference_image` 必填 |
| Client → Backend | `POST /api/companion/wardrobe/preview` `{description, image?, content_type?, feedback?}` | 换装预览（写 temp-media，不入库）；后端用一次 LLM 路由调用把描述分类为 `texture`（仅改色/材质/图案）、`garment`（改轮廓/新增件）或 `accessory`（包/帽/眼镜等硬质挂件），再下发到对应流水线 | 同步；返回 `WardrobePreviewResponse`，其中 `kind` / `mesh_url` / `mesh_file_id` / `assembly_json` 在走几何流水线时填充（装配语义见 §1.6）；客户端不感知路由决策；`file_id` / `mesh_file_id` 在 `temp_file_ttl_hours` 内可被 `confirm` 落库 |
| Client → Backend | `POST /api/companion/wardrobe/confirm` `{file_id, name, prompt?, normal_file_id?, roughness_file_id?, metalness_file_id?, mesh_file_id?, assembly_json?}` | 把预览产物落为 `WardrobeItem` + 自动装备（同槽互斥，§1.6）+ emit `wardrobe.updated` | `file_id`/`mesh_file_id` 已过期/不存在 409；返回 `WardrobeItemResponse`(含 `kind` / `mesh_url` / `assembly_json`) |

### 1.2 事件类型

| 事件 `type` | 触发时机 | 关键 payload 字段 | 消费者 |
|------------|----------|-------------------|--------|
| `companion.affect` | 非言语的情境化情绪反应 | `{emotion, locale?, target?}` | EMOTIONAL 状态切换;quiet 档也透传（断消息不断 affect） |
| `avatar.regenerated` | `avatar.regenerate` 最终结果 | `{job_id, asset_url?, seed_front_url?, seed_right_url?, seed_back_url?, id?, error?}` | 替换头像或展示失败提示 |
| `model.ready` | 3D 模型异步生成就绪 | `{model_id, asset_url, species?, rig_type?, provider?}` (`provider` ∈ `"tripo_image_to_3d"` / `"tripo_multiview_to_3d"` / `"blender_llm"`) | 客户端加载 GLB + 注入 TS 动画 clip + 状态机绑定;`provider` 字段供 UI 标识生成来源（Tripo 单图/多视图 vs LLM 自建模） |
| `model.gen.progress` | 模型生成中 | `{progress: 0..100, stage, provider?}` (`provider` ∈ `"tripo"` / `"blender_llm"`) | 可选进度展示;`provider` 字段让客户端识别当前是 Tripo 多步流水线还是 Blender+LLM 迭代循环（最坏 ~100 分钟） |
| `model.failed` | 模型生成失败 | `{error}` | 渲染程序化蛋形兜底角色（无气泡、无错误） |
| `wardrobe.updated` | 换装产物就绪 | `{}` | 客户端重新拉取 wardrobe 列表；渲染层按 item `kind` 自动分派贴图热替或几何装配（rebind 到身体骨骼）——两者均不动身体骨骼动画与 morph |
| `video_gen.completed` | 视频生成成功 | `{task_id, url}` | 媒体展示 |
| `video_gen.failed` | 视频生成失败 | `{task_id, error}` | 用户可见错误 |
| `reload.mcp` | MCP 配置变更后服务器主动通知 | `{}` | Client 转发给 Runner,Runner 重新加载,再 `tools.sync` 回 backend |

**事件投递范围（`session_id` 语义）**：

WS 事件分两类，按投递边界不同携带或不携带 `session_id`：

| 事件类别 | `session_id` | 说明 |
|----------|--------------|------|
| **聊天会话事件**（`message.start` / `message.delta` / `message.complete` / `tool.start` / `tool.call` / `tool.complete` / `error`） | **必带** | 由 `JsonRpcEmitter` 在某个具体会话上发出——值为该会话的 `Conversation.id` 字符串。这些事件**只属于该会话**，不应对其他会话可见。 |
| **outbox 事件**（`companion.affect` / `companion.message` / `model.ready` / `model.failed` / `model.gen.progress` / `wardrobe.updated` / `wardrobe.gift` / `avatar.regenerated` / `video_gen.*` / `reload.mcp`） | **不带** | 由 WSEvent outbox 投递到用户的 desktop，是"伙伴对自己的全用户行为"，与当前打开哪个会话无关。 |

**渲染端约定**：聊天会话事件必须按 `session_id === $chatSessionId.get()` 过滤。例如 cron 自主轮次（`Conversation.kind='cron'`，renderer 不挂载）的 `message.complete` 携带 `session_id=cron_id`，渲染端必须丢弃——否则 cron 助手文本会以"回复"形式出现在用户当前所看的会话里。Outbox 事件无 `session_id`，照常处理（即 TTS / 气泡 / wardrobe 热替 / 模型重载等）。

### 1.3 Affect 契约

**语义/渲染解耦**——Backend 只产出 emotion + 可选 locale 语义,绝不指定渲染方式或像素坐标。

**provider 字段**（3D 模型生成，`model.ready` / `model.gen.progress` 事件 payload）：

| 取值 | 触发条件 | 模型质量预期 | 典型时长 |
|------|---------|-------------|---------|
| `"tripo_image_to_3d"` / `"tripo_multiview_to_3d"`（`model.ready`）/ `"tripo"`（progress） | 默认走 Tripo3D，且 key + credits 充足。`image_to_3d` 用于单视图模式，`multiview_to_3d` 用于多视图模式 | 高（PBR 纹理、精细几何） | 数分钟 |
| `"blender_llm"` | `provider="blender_llm"` 显式选择 / `tripo_api_key` 缺失 / 余额耗尽（含 `_is_credits_exhausted_error` 模式匹配）/ 余额预检返回 0 | 中低（LLM 自建模，无 PBR，几何自由形式） | 最坏 ~100 分钟（10 轮 × 10 分钟 Blender timeout） |

`ModelGenerateRequest.provider` 可选值：`"tripo"` / `"blender_llm"` / `None`（auto-detect，遵循上表优先级）。Client 应据 `model.gen.progress` 携带的 `provider` 字段提示用户预期时长。

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
| 换装产物（纹理 + 服装 GLB） | `companion-assets/` | 5 分钟 |

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

### 1.6 换装单元装配契约

`WardrobeItem.kind ∈ {"texture", "garment", "accessory"}` 由后端路由决定（客户端不发送、不感知路由参数）。几何单元（garment/accessory）的装配语义由 `assembly_json`（DB 列）与服装 GLB `scene.extras["dsh:assembly"]` **双处声明且必须一致**——DB 供列表/装备决策（免解析 GLB），extras 保证 GLB 自描述；冲突时以 DB 为准。

```jsonc
{
  "kind": "garment",              // texture | garment | accessory
  "slot": "torso",                // 互斥槽位：outfit | full_body | torso | legs | feet | head | hands | back
  "layer": 1,                     // 叠放顺序，越小越贴身；同槽互斥时恒为 1，预留跨槽排序
  "socket": null,                 // accessory 挂点骨骼名（身体 skeleton 中的实际骨骼名）；garment 恒为 null
  "physics": "skin",              // skin（蒙皮跟随）| cloth（客户端 verlet 布料摆动）
  "materials": { "*": { "albedo": true, "normal": true, "roughness": true, "metalness": true } }
}
```

**装备语义（多件混搭）**：`equipped` 布尔保留在每行上，但互斥范围是**槽位**——装备一件只顶掉同槽已装备件，异槽并存。`slot` 判定规则：`kind=texture` 恒为 `outfit`；几何单元读 `assembly_json.slot`（缺省 `torso`）。persona 的 outfit 描述字段镜像**全部已装备件**的拼接。

**socket 匹配**：客户端按骨骼名在身体 skeleton 精确匹配，失败时忽略 `mixamorig:` 前缀做后缀匹配；均失败时挂件退化为静态摆放并打告警日志（导出端应视为回归信号）。

**GLB 导出不变量**（违反即客户端装配错位，生成管线负责保证）：

| 单元 | 不变量 |
|------|--------|
| garment | `skins[].joints` 骨骼名与顺序与身体 GLB **完全一致**（客户端 rebind 零映射的前提）；无动画、无 morph |
| accessory | 不含 skins（纯静态 mesh，佩戴点已在导出时对准挂点骨骼位置）；无动画、无 morph |

**降级**：服装/挂件 GLB 载入失败时退化为贴图换装或程序化兜底，不崩溃（沿用 §1.2 `model.ready` 的兜底原则）。

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
| `deskagent.config.update` | Client → Runner | 推送完整配置 dict;Runner 持有在内存,下次 `load_config()` 即生效,无需重启 | §2.4 |
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

### 2.4 `deskagent.config.update` — Runner 配置推送

Client 是配置的唯一拥有者,经此方法把完整配置 dict 推送给 Runner。Runner 持有在内存(`utils.config._INMEMORY_CONFIG`),每次工具调用经 `load_config()` 读取——**不再读取磁盘文件**。

**时序**:Runner 就绪 handshake 后、首个 `execute_tool` 前,Client 推一次 full config。此后每次用户在设置页保存,Client 再推一次。Runner 进程重启后内存配置清空,Client 在下次 `runner_ready` 时重新推送。

**Payload**:

```json
{
  "method": "deskagent.config.update",
  "params": {
    "config": { /* 见下文 schema */ }
  }
}
```

**Config schema**(顶层 mapping;所有 key 可选,缺省走 Runner 代码默认值):

| Section | Key | 类型 | 说明 |
|---------|-----|------|------|
| `terminal` | `env_type` | `local\|docker\|ssh\|singularity` | 终端环境类型 |
| | `timeout` | int | 默认超时(秒) |
| | `cwd` | str | 默认工作目录(留空用当前目录) |
| | `timezone` | str | 时区(留空用系统) |
| | `sudo_password` | str | sudo 密码 |
| | `interactive_sudo_prompt` | bool | 交互式 sudo 提示 |
| | `docker_binary` | str | Docker 二进制路径(留空自动检测) |
| | `git_bash_path` | str | Git Bash 路径(仅 Windows) |
| | `sandbox_dir` | str | 沙箱目录 |
| | `shell_init_files` | list | Shell 初始化文件列表 |
| | `auto_source_bashrc` | bool | 自动 source .bashrc |
| | `env_passthrough` | list | 传递到子进程的环境变量 |
| | `credential_files` | list | 挂载到容器的凭据文件 |
| | `singularity.scratch_dir` | str | Singularity scratch 目录 |
| `security` | `write_safe_root` | str | 写安全根目录 |
| | `redact_secrets` | bool | 敏感信息脱敏 |
| | `github_token` | str | GitHub Token(tirith 安全检查) |
| | `website_blocklist` | dict | 网站访问黑名单(`{enabled, domains, shared_files}`) |
| `browser` | `engine` | `auto\|lightpanda\|chrome` | 浏览器引擎 |
| | `command_timeout` | int | 命令超时(秒) |
| | `inactivity_timeout_seconds` | int | 会话无活动超时(秒) |
| | `record_sessions` | bool | 录制浏览器会话 |
| | `allow_private_urls` | bool | 允许访问私有 URL |
| | `executable_path` | str | 浏览器可执行文件路径 |
| | `playwright_browsers_path` | str | Playwright 浏览器路径 |
| | `cdp_url` | str | CDP URL |
| | `camofox.url` | str | Camofox 服务 URL |
| `auxiliary.vision` | `timeout` | int | 视觉模型推理超时(秒) |
| | `temperature` | float | 视觉模型推理温度 |
| | `download_timeout` | int | 下载超时(秒) |
| `auxiliary.audio.tts` | `default_voice` | str | 默认 Piper voice(留空用 bundled zh_CN) |
| `skills` | `github.token` | str | GitHub PAT(技能市场) |
| | `github.app_id` | str | GitHub App ID |
| | `github.private_key_path` | str | GitHub App 私钥路径 |
| | `github.installation_id` | str | GitHub App installation ID |
| | `disabled` | list[str] | 禁用的技能 leaf 名 |
| | `env_overrides` | dict | 技能环境变量覆盖 |
| | `external_dirs` | list | 外部技能目录 |
| | `guard_agent_created` | bool | 安全扫描 agent 创建的技能 |
| `toolsets` | `disabled` | list[str] | 禁用的工具集 ID |
| `mcp_servers` | (mapping) | dict | MCP server 配置(key=server 名) |
| `debug` | `interrupt` | bool | 调试中断模式 |
| | `vision_tools` | bool | 视觉工具调试 |
| `file_state` | `disabled` | bool | 禁用跨 agent 文件状态跟踪 |
| `computer_use` | `backend` | `auto\|cua\|win\|noop` | 后端选择 |
| | `cua_driver_version` | str | CUA 驱动版本 |
| | `cua_driver_cmd` | str | CUA 驱动命令 |
| | `cua_telemetry` | bool | cua-driver PostHog 遥测 |
| `osv` | `endpoint` | str | OSV API 端点 |
| `tool_output` | `max_bytes` | int | 最大输出字节数 |
| | `max_lines` | int | 最大行数 |
| | `max_line_length` | int | 最大行长度 |
| `file_read_max_chars` | int | — | 单次文件读取最大字符数 |

**持久化**:Client 把配置以 JSON 存储在 `$DESKAGENT_HOME/desktop-settings.json`(非用户面向)。Runner 不再读写任何配置文件。

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

激活码（base64 编码的 `{baseUrl, token}` JSON）经 Electron `safeStorage` 加密落盘（`agent-session.json`，schema v2）:
- Windows: DPAPI
- macOS: Keychain
- Linux: libsecret（但 Runner/Desktop 不支持 Linux,仅作原理说明）

session JWT **仅在内存中**持有——每次启动时用存储的激活码调 `/api/user/activate` 获取新的 session JWT。激活码是持久凭证；session JWT 用于日常 API 调用与 ws-ticket 签发。

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
