# SpiritAgent 跨模块协议契约

> 本文收纳 Backend ↔ Client ↔ Runner 之间**跨模块共享的契约**。核心目的：当你改某个功能时，提醒你同时兼顾多个模块，避免只改一处导致遗漏。
> 架构动机（为什么这样设计）见 [ARCHITECTURE.md](ARCHITECTURE.md)；产品设计意图见 [DESIGN.md](DESIGN.md)；实现细节、文件路径、错误码、配置项见各模块 [README.md](README.md)。

## 0. 契约总览

全链路采用 **JSON-RPC 2.0** 封装双向流量，三类链路共用同一信封：

| 链路 | 方向 | 传输 | 鉴权 | 见 |
|------|------|------|------|----|
| Backend ↔ Client | 双向 | WebSocket 长连接（/api/chat/ws） | 用户 JWT | §1 |
| Client ↔ Runner | 双向 | 本地 OS IPC（Windows 命名管道 / macOS UDS）承载 WebSocket 帧 | 每次启动握手 token（失败 401） | §2 |
| Runner → Client → Backend（反向 RPC） | Runner → Client → Backend | 嵌套在 §2 上，经 Client 转发到 /api/llm/completion | Client JWT | §3 |

**信封四种形态**（JSON-RPC 2.0）：请求（带 id + method + params）、事件（无 id、method=event、带 type + payload + seq，不可被响应）、响应（带 id + result 或 error）、ACK（带 method=session.ack、params={seq: int}）。

**核心约定**：
- call_id 是整张表的**唯一 Future Key**——Backend 按 (user_id, call_id) 寻址，跨用户不共享。
- 事件无 id，不可被响应；请求与响应必须按 id 配对。
- 所有下发事件均附加递增序列号 `seq`（从 1 开始）；序列号与客户端 `lastReceivedSeq` 均为**连接级（Connection/User 级）**状态，跨 Session 共享。客户端维护 `lastReceivedSeq` 保证去重与有序消费。
- 客户端定期向服务端发送 `session.ack(seq)` 确认消费进度（带 id 的标准 RPC 请求），服务端自 Replay Buffer 中修剪已确认帧。
- **断线补偿与 30s 缓冲期（Grace Period）**：WS 断开后 Backend 保留调度器、生成任务与未决 IPC future 30 秒；客户端在 30 秒内重连并发送 `session.resume(session_id, last_seq)`。若在缓冲期内且缓存未溢出，服务端无缝重放断线期间缺失帧（`resumed: true`），保持流式对话不中断；若超时、溢出或服务端重启导致序列号失同步，则返回 `resumed: false`、当前最大 `current_seq` 与完整 DB 历史进行全量重水化。重连且收到 `resumed: false`（或降级走 `session.get_main`）时，客户端**必须**重置 `lastReceivedSeq = current_seq` 以防旧水位导致事件黑洞；普通会话切换（活连接上）严禁重置水位。
- WS 关闭码 1008（鉴权失效）= 立即退出重连流程，不再尝试。

---

## 1. Backend ↔ Client 契约

### 1.1 通道分工（WS vs REST 路由原则）

Backend ↔ Client 同时暴露 JSON-RPC over WebSocket 与 HTTP REST。两套通道的**设计意图不同**——选错通道等于把一类语义错配到不属于它的链路：

| | WS（JSON-RPC） | REST |
|---|---|---|
| **设计意图** | 绑定进程内上下文的持续推送通道——事件、进度与小包数据流以 WS 为单一推送路径，关 WS 即丢弃运行时状态 | URL 寻址、无状态 CRUD——任何持有 JWT 的入口（Hub、CLI、脚本、第三方集成）可独立调用，与 chat 是否在线无关 |
| **承载语义** | 长会话、跨多次往返、需要进程内锚点的小包 | 幂等 CRUD、可独立寻址的对象、多 KB-MB 载荷上传/下载 |

**判别启发（顺序问）**：依赖进程内状态吗 → WS；需要在 chat 未连接时执行吗 → REST；是生产者/进度/状态推送吗 → 通知侧永远经 WS outbox + 事件帧下发（与命令端走哪条无关）。

**REST 镜像特例**：某条 WS 方法的读被一个不持有 WS 连接的 UI 表面（典型为 Hub）需要时，可保留 REST 镜像。镜像必须包装同一个服务函数、两端契约等价，任何漂移都要双端同步。

### 1.2 伙伴生命周期方法（方法级契约）

普通 chat / tool 类方法见 [backend/README.md](backend/README.md)。以下是**伙伴生命周期**专用方法，Client 必须实现消费状态机（详见 [DESIGN.md §5–§6](DESIGN.md)）。**逐参数签名见 backend 代码，本文只锁定契约意图与「改这里要同步哪里」：**

| 方法 | 用途 | 改动需同步的模块 |
|------|------|------------------|
| onboarding.get_state / onboarding.submit 与 GET /api/companion/onboarding/state | 查询/增量提交 onboarding 答案（断点恢复，支持 WS RPC 与 REST） | Backend 状态机 + Client 消费状态机 + DESIGN §5 流程 |
| avatar.regenerate | 重生头像（不使模型失效） | Backend + Client 头像展示 |
| tts.match_voice / tts.design_voice / tts.list_voices | 音色描述匹配 / 专属音色生成 / 目录枚举 | Backend TTS + Client 音色页 + 工具窗口 REST 镜像 |
| companion.set_disturbance_tier | Client 上报生效打扰档位（Client 是唯一权威） | Backend 持久化 + Client 活动感知 + DESIGN §6.2 |
| companion.check_affect / companion.interact / companion.should_act / companion.record_interaction_stats / companion.get_user_profile | 情境化情绪 / 戳反应 / 自主空间决策 / 互动统计 / 画像召回 | Backend 推理 + Client 触发与消费 + DESIGN §6.3/§6.4 |
| POST /api/companion/portrait/confirm | 确认形象（幂等），解开音色/用户子阶段 | Backend 状态 + Client 流程 |
| GET/POST /api/companion/model | 查询 / 触发 3D 模型异步生成（文生3D：视觉 LLM 读头像 + 角色设定构建提示词，无种子图） | Backend 生成管线 + Client 加载 + DESIGN §5.6 |
| companion.model.retryDownload | 仅重试下载已付费的 3D 生成结果（provider query 刷新过期 URL + 下载 + 后处理；**绝不重新提交生成/计费**） | Backend 生成管线 + Client 失败态入口 |
| POST /api/companion/sprite | 静态精灵相册解析（降级渲染源） | Backend 生成 + Client 降级层 + DESIGN §1.2 |
| POST /api/companion/avatar（含 /from-image）、/avatar/{id}/select 与 GET /avatar/history | 半身头像生成（含上传参考图重绘）/ 历史形象切换激活 / 历史查询 | Backend 生成 + Client 头像确认与历史画廊 + DESIGN §5.4 |
| POST /api/companion/wardrobe/preview 与 GET .../preview/{job_id} 与 POST .../wardrobe/confirm | 换装预览（入队/轮询）与落库装备 | Backend 流水线 + Client 装配层 + DESIGN §1.3 + §1.8 状态机 |

**关键约束**（跨模块语义，非实现细节）：
- **断点恢复**：角色子阶段答完即标记角色已定稿；onboarding 整体只在形象确认且音色 + 用户信息齐后才算完成；未确认形象时回到头像确认步恢复，确认后按音色先于用户信息路由。
- **形象锁定**：头像确认即锁定，物种/性别/基础外貌不可再改，3D 模型/头像重新生成路径关闭；换装与动画生成不受影响。
- **换装预览为 202 异步**：校验图片后入队，结果经轮询或事件等价获取；预览产物在 TTL 内可落库。
- **下载失败可恢复（已付费结果绝不丢）**：3D 生成成功后、下载开始前，provider task id 与下载 URL 已持久化；下载或本地后处理失败只置 `download_failed` 并随 `model.failed` 事件下发 `retry_download: true` + `model_id`——客户端必须据此提供"重试下载"入口（`companion.model.retryDownload`），而非引导重新生成。重试路径只调 provider 查询与下载接口，服务重启中断的下载同样进入该可恢复态。

### 1.3 事件类型

| 事件 type | 触发时机 | 消费者 |
|-----------|----------|--------|
| companion.affect | 非言语的情境化情绪反应 | Client 切 EMOTIONAL（安静档也透传） |
| avatar.regenerated | 头像重生最终结果 | Client 替换头像或展示失败 |
| model.ready / model.gen.progress / model.failed | 3D 模型就绪 / 进度 / 失败 | Client 加载 + 绑定动画 / 进度展示 / 降级 |
| wardrobe.updated | 换装产物就绪 | Client 重拉列表 + 分派热替/装配 |
| companion.assets.updated | 伙伴实时创建了新表情/动画（create_expression / create_animation） | Client 重拉 /animations + /expressions，绑定到 3D |
| wardrobe.preview.progress / .ready / .failed | 换装预览 job 状态 | Client UI 反馈（与 GET 轮询等价） |
| video_gen.completed / .failed | 视频生成结果 | 媒体展示 |
| reload.mcp | MCP 配置变更后通知重载 | Client 转发 Runner，重载后回同步工具表 |

**事件投递范围（session_id 语义）**：session_id 就是 conversation_id 的字符串形式（见 §6）。聊天会话事件（message.* / tool.* / error）必带 session_id、只属于该会话，渲染端必须按 session_id 过滤；outbox 事件（上表）不带 session_id，投递到该用户的 desktop、与打开哪个会话无关，照常处理。

### 1.4 Affect 与空间契约

**语义/渲染解耦**——Backend 只产出情绪 + 可选场所语义，绝不指定渲染方式或像素坐标。

**emotion 枚举**（22 项，权威源 backend/services/chat/affect.py 的 BUILTIN_EMOTIONS）：happy / sad / surprised / excited / confused / concerned / shy / proud / grateful / playful / bored / lonely / sleepy / curious / embarrassed / apologetic / neutral / pout / angry / smug / scared / relieved。

**locale 枚举**（5 项，权威源 ALLOWED_LOCALES）：home / chat / perch / roam / sleep。

**spatial target**（可选，仅 perch 时有意义）：窗口/进程名关键字。Client 经窗口枚举解析为窗口几何后计算 perch 点。注意：此处的 target 是空间 cue 的**窗口关键字**，与 Client 内部的场所 target（仪式行走目的地）是**两个不同概念**——后者由工具调用本地触发、不在本协议枚举内（见 [DESIGN.md §3.2](DESIGN.md)）。

**inline 空间 cue 规则**：LLM 在回复前自填空间 cue，由解析器解析后附加到 message.complete 的场所/目标字段。Backend 解析后下发，Client 决定是否落位（档位门控 + 对话开启抑制）。

**动作 tag（[action:NAME]）**：LLM 可另附一个结构化动作名（snake_case），Backend 在提示词中注入可用清单（内置 procedural clip + 模型生成 clip 的并集），解析后经 message.complete 的 affect.action 字段下发，Client 按名称/标签匹配 clip、失败回退到 emotion valence。LLM 找不到合适动作时可调用 create_animation 工具实时生成新 clip 并落库。

**连续气泡分隔**：LLM 需要在一回合内连发多条短回复时，用单独一行 `---` 分隔；Backend 流式解析为 `message.break` 事件（带 session_id），Client 收尾当前气泡、停顿 0.5–1.5s 后再渲染下一气泡。

**扩展协议**：每次扩展 emotion / locale 须同步更新 **Backend 白名单 + 客户端表情/场所映射 + 本文档**三处；未覆盖项一律按 neutral / home 处理。

### 1.5 资产 URL 签名与传输缓存

| 资产 | TTL |
|------|-----|
| portrait 头像 | 5 分钟 |
| 3D 模型 GLB | 5 分钟 |
| 换装产物（纹理 + 服装 GLB） | 5 分钟 |
| 静态精灵相册 PNG | 5 分钟 |

**契约要点**：资产端点支持双通道鉴权——已登录 Client 携带有效 Bearer JWT 时可直接访问归属资产；未携带令牌时按 URL HMAC 签名校验（每次签名 5 分钟 TTL，换设备/过期需重新签名）。服务端模型/资产端点支持 HTTP Range 断点续传 + ETag + 不可变缓存头；Client 按内容哈希（SHA-256）在本地磁盘缓存，命中即跳过网络，未命中/中断走断点续传。

### 1.6 错误信封

REST 端点异常路径返回统一结构：error（短码）+ reason（分类，可空）+ status（HTTP 状态）。WS JSON-RPC 错误使用标准错误码（-32700 到 -32603）。**关键契约**：内部错误抛至前端前必须脱敏，严禁包含数据库账号、服务器本地路径等栈帧细节；21 种分类（FailoverReason）决定恢复策略，见 [backend/README.md](backend/README.md)；流式 chat 一旦首 chunk 已发，任何 provider 失败不再 fallback。

### 1.7 换装单元装配契约

换装单元 kind 取 texture / garment / accessory，由 Backend 路由决定（Client 不发送、不感知路由参数）。几何单元（garment/accessory）的装配语义由数据库列与服装 GLB 的自描述元数据**双处声明且必须一致**——数据库供列表/装备决策（免解析 GLB），GLB 自描述保证可移植；冲突时以数据库为准。

装配语义要点：
- **槽位（slot）**：outfit / full_body / torso / legs / feet / head / hands / back。装备互斥范围是**槽位**——装备一件只顶掉同槽已装备件，异槽并存。texture 恒为 outfit；几何单元读自身声明（缺省 torso）。
- **叠放（layer）**：升序叠放，贴身衣物先挂；同槽互斥时恒为 1。
- **挂点（socket）**：accessory 挂到身体骨架的实际骨骼名；garment 恒无。客户端按骨骼名精确匹配，失败时忽略前缀做后缀匹配；均失败退化为静态摆放并告警。
- **物理（physics）**：skin（蒙皮跟随）| cloth（客户端布料摆动）。
- **GLB 导出不变量**（违反即客户端装配错位）：garment 的蒙皮骨骼名与顺序须与身体模型完全一致、无动画无表情；accessory 不含蒙皮（纯静态网格、佩戴点已在导出时对准）、无动画无表情。
- **降级**：服装/挂件载入失败退化为贴图换装或程序化兜底，不崩溃；身体资产整体缺失按「3D 模型 → 静态精灵相册 → 程序化蛋形」层级兜底。

### 1.8 Render job 状态机（换装预览）

分钟级生成任务分两段：web 进程入队（同步、毫秒级），Render Worker 认领执行（分钟级）——涵盖换装预览等 Blender 后处理与 3D 模型的文生3D provider 管线。对外契约只涉及换装预览 job，状态为 queued → processing → succeeded / failed；失联的 processing 行按阈值回收，未超认领封顶重排队、超了转 failed。客户端只消费状态、不感知恢复。3D 建模共用同一队列语义，但对外仍是 model.gen.progress → model.ready / model.failed 事件、无轮询端点。

---

## 2. Client ↔ Runner 契约

### 2.1 链路与鉴权

- Runner **主动**连 Client 提供的 IPC 端点（Windows 命名管道 / macOS UDS，权限 0600）。
- 端点路径与 token 由 Client **单向下发**（启动参数 + 落盘文件）；Runner 重连间重读文件以在 Client 重启后拾取新端点与新 token。
- 启动后发 runner_ready 握手通知；鉴权走 upgrade 头，校验失败 Client 回 401、不完成握手；Runner 收到 401 后丢弃内存缓存端点与 token、等待重读文件。token 为每次启动新生成的 256-bit 随机值，**不是 Backend 凭据**。
- 安全模型：Windows 命名管道命名空间对本机进程可枚举、且无自定义 DACL 接口——token 是实际闸门；macOS 侧 0600 socket 为主闸门、token 为纵深防御。OS IPC 不经网络栈，无端口监听面。

### 2.2 RPC 方法清单

| 方法 | 方向 | 用途 | 改动需同步的模块 |
|------|------|------|------------------|
| runner_ready | Runner → Client | 启动握手，携带 version + capabilities | Runner 探测 + Client 功能门控 |
| tools_changed | Runner → Client | 工具 schema 变更通知，Client 重拉并同步到 Backend | Runner + Client + Backend 工具表 |
| get_tools | Client → Runner | 获取工具 schema（已过滤禁用项） | Runner 过滤 + Client + Backend |
| spiritagent.info | Client → Runner | 完整运行快照 | Runner 上报 + Client 诊断 |
| execute_tool | Client → Runner | 执行工具调用 | Backend 路由 + Client 中转 + Runner 执行 |
| mcp.reload | Client → Runner | 重载 MCP server 配置 | Client 配置推送 + Runner 重载 |
| spiritagent.cancel | Client → Runner | 置全局中断标记（异步生效） | Client 中断 + Runner 轮询 |
| spiritagent.config.update | Client → Runner | 推送完整配置（Client 是唯一拥有者） | Client 设置 + Runner 内存配置 |
| request_llm | Runner → Client | 反向 RPC 借大脑 | §3 |

### 2.3 runner_ready capabilities 与 health 状态

capabilities 字段由**运行时探测**：microphone / screen_capture / system_activity 真实枚举设备、调用底层 API；local_stt / local_tts 执行原生加载器的 import 探测。**不用存在性检查**——那会欺骗 UI 让用户点不能用按钮。`runner_ready` 与 `spiritagent.info` 同时返回平铺的 `capabilities`（布尔映射向后兼容）与结构化的 `capabilities_health`（`{ [capability_name]: { available: boolean, reason?: string } }`），供客户端对各子能力展示精细化诊断与局部优雅降级。`probe_failed=true` 仅在探测流程发生致命未捕获异常时置位。语音通话 / 唤醒词在对应 capability 为 false 时由 Client 优雅降级或提供具体故障原因 Tooltip 引导。

### 2.4 配置推送所有权

**Client 是配置的唯一拥有者**，经 spiritagent.config.update 把完整配置 dict 推送给 Runner。Runner 持有在内存、每次工具调用读取——**不再读写磁盘配置文件**。时序：Runner 就绪握手后、首个 execute_tool 前推一次 full config；此后每次设置页保存再推一次；Runner 重启后内存配置清空，Client 在下次 runner_ready 时重新推送。Client 把配置以 JSON 存储在用户主目录（非用户面向）。**config schema 的键明细见 runner 代码（utils/config.py），本文只锁定所有权与推送时序契约。**

---

## 3. 反向 RPC 桥接（Runner 借大脑）

**核心约束**：Runner **零凭证运行**，不持有任何 Backend Token；所有出站 LLM 请求必须向上借道 Client。本地 IPC 链路的握手 token 只守 Client↔Runner 之间，不是 Backend 凭据，不参与任何 Backend 请求的鉴权。

**链路**：Runner ──(本地 WS: request_llm)──> Client ──(HTTP POST: /api/llm/completion)──> Backend ──> LLM（Client JWT 鉴权）。

**速率守卫**：Client 转发前统计单会话请求次数与载荷大小（硬上限 200 帧 / 1MB），防止 Runner 工具逻辑失控刷爆 LLM 额度。

---

## 4. IPC Future 桥接（Backend 侧契约）

call_id 是 Backend IPC future 字典的**唯一 Future Key**，标识单次 RPC 生命周期。

**键结构**：按 (user_id, call_id) 二元组寻址，而非单 call_id——并发用户不共享 future；user_id 来自 JWT 解析（受保留键保护）；WS 断开时取消该用户所有未决 future。

**超时与快速失败**：默认 300s 超时返回 synthetic error；下发前做连接在线 → 工具可用 → 发送异常三层检查，通常毫秒级返回离线错误，仅绕过三层后才进入超时。

**JWT 过期边界**：token 在飞行途中过期时 Client 回传被拒 → future 挂起直到超时；token 过期不触发 WS 断开，当前靠超时兜底。

**通用事件分发**：复用同一 future 通道发任意 JSON-RPC 事件（如 reload.mcp）。

---

## 5. 跨模块安全契约

> 架构原则（物理隔离、防御纵深）见 [ARCHITECTURE.md §7](ARCHITECTURE.md)；本节锁定跨模块**契约**。主动消息的 outbox 下发机制见 [ARCHITECTURE.md §5](ARCHITECTURE.md)，此处不重复。

### 5.1 Reserved Keys（防 LLM 入参注入）

LLM 工具入参**禁止**覆盖保留键：user_id / llm_config / user_settings（Backend 在工具入口静默丢弃）。**角色定义同等保护**：角色定义作为系统提示词的一部分，同样受此保护，防止用户对话内容注入改写伙伴人格。**新增保留键须在本文档 + 工具入口两处同步。**

### 5.2 不可信工具结果包裹

外部（Web 搜索 / 浏览器抓取 / MCP 外部资源）获取的字符串注入 LLM 上下文前强制包裹。短字符串不包——注入风险低 + 节省 token。

### 5.3 凭据落盘

激活码（base64 编码的 {baseUrl, token}）经 Electron safeStorage 加密落盘：Windows DPAPI / macOS Keychain（Linux 仅原理说明，Runner/Desktop 不支持）。session JWT **仅内存持有**——每次启动用激活码换新 session JWT；激活码是持久凭证，session JWT 用于日常 API 调用与 ws-ticket 签发。渲染与预加载进程不可访问 safeStorage 接口，阻断 XSS 窃取凭证。

### 5.4 API Key Fingerprinting

用户的模型配置仅经管理端点管理（`/api/admin/model-configs` 系）；Client 无自助配置入口。原始 API key 永不离开后端：管理列表只返回 *_set 布尔 + 指纹（sk-…XX 形式），PUT 空 api_key = 保留原值（管理页看不到原始 key，留空必须等价于"不改"）。admin 写入强制 LLM 三字段（base_url / api_key / model_name）非空——半行配置会静默打断用户会话链。

### 5.5 自更新签名（Client ↔ Backend / Installer ↔ Backend）

| 通道 | 校验 |
|------|------|
| Electron 二进制自更新 | electron-updater RSA |
| Runner wheel 自更新 | SHA-512 + 公钥 RSA 双重校验（签名不匹配在 Staging 阶段直接拦截） |
| Skills | 由 installer 首装 seed，client 自更新不下载 |

**两阶段更新契约**（避免升级中途断网/崩溃变砖）：Stage 1 预取（下载新版 Electron + Runner wheel 到 staging，强校验签名 + SHA-512，写 Sentinel）；Stage 2 安装（用户点 Restart & Install 后原地覆盖、导入冒烟测试、失败回滚旧版）。**核心约束**：Runner venv 目录**永不**重命名或移动，确保任意升级阶段崩溃时旧版 Runner 依赖树仍完全可用。

---

## 6. ID 语义

| ID 类型 | 格式 | 生命周期 | 唯一性范围 |
|--------|------|----------|------------|
| conversation_id | 整型 | 单次会话 | 全局唯一，DB 主键 |
| session_id | 字符串 | 与会话同生命周期（= conversation_id 的字符串形式，跨 WS 重连不变） | 全局唯一 |
| call_id | 字符串 | 单次 RPC 调用 | 整张表唯一（用作 Future Key） |
| task_id（视频生成） | 字符串 | 异步任务周期 | 单 (user_id, provider) 内唯一 |

**职责分立**：session_id 就是 conversation_id 的字符串形式——Client 侧始终用字符串、Backend 侧持久化为整型，通信边界完成两者转换；call_id 作为唯一 Future Key 标识生命周期（见 §4）。

---

## 7. 跨模块语言规则

LLM 在生成 affect / spatial 时按以下规则：
- affect 的 emotion 必须从 §1.4 枚举集选，LLM 在回复前自填，由解析器解析。
- spatial 的 LOCALE 从 §1.4 locale 枚举选，target 可选、仅 perch 时有意义。
- **Backend 不产出像素坐标**——Client 据 locale + 当前空间状态决定最终位置与移动方式；target 仅在 perch 时由 Client 经窗口枚举解析为窗口几何后计算 perch 点。

---

## 8. 维护规约

- 本文档是**跨模块公共契约**——任何改动必须同时通知所有受影响的模块所有者。
- 任何扩展 emotion / locale / 事件 type，必须在 **本文档 + Backend 白名单 + Client 消费代码**三处同步。
- 任何 Reserved Key 新增，必须在 **本文档 + 工具入口** 同步。
- 任何换装装配语义（slot/layer/socket/physics）变更，必须在 **本文档 + Backend 生成管线 + Client 装配层**同步。
- 子模块 README 不重复本文档内容，只在需要时链接。
