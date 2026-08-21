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
- call_id 是整张表的**唯一 Future Key**——后端按 (user_id, call_id) 寻址，跨用户不共享。
- 事件无 id，不可被响应；请求与响应必须按 id 配对。
- 所有下发事件均附加递增序列号 `seq`（从 1 开始）；序列号与客户端 `lastReceivedSeq` 均为**连接级（Connection/User 级）**状态，跨 Session 共享。客户端维护 `lastReceivedSeq` 保证去重与有序消费。
- 客户端定期向服务端发送 `session.ack(seq)` 确认消费进度（带 id 的标准 RPC 请求），服务端自重放缓冲中修剪已确认帧。
- **心跳保活（session.ping）**：客户端在连接空闲 15s 时发送 `session.ping`（带 id 的标准 RPC 请求），服务端回 `{}`；若 30s 内无任何帧到达，客户端判定半开连接并主动 `close(4000, 'heartbeat')` 触发重连。该机制覆盖 NAT 超时、Wi-Fi 切换、VPN 抖动、笔记本合盖等场景，避免用户发消息后 120s 死寂。
- **断线补偿与 30s 缓冲期（Grace Period）**：WS 断开后后端保留调度器、生成任务与未决 IPC future 30 秒；客户端在 30 秒内重连并发送 `session.resume(session_id, last_seq)`。若在缓冲期内且缓存未溢出，服务端无缝重放断线期间缺失帧（`resumed: true`），保持流式对话不中断；若超时、溢出或服务端重启导致序列号失同步，则返回 `resumed: false`、当前最大 `current_seq` 与完整 DB 历史进行全量重水化。重连且收到 `resumed: false`（或降级走 `session.get_main`）时，客户端**必须**重置 `lastReceivedSeq = current_seq` 以防旧水位导致事件黑洞；普通会话切换（活连接上）严禁重置水位。
- WS 关闭码 1008（鉴权失效）= 立即退出重连流程，不继续尝试。

---

## 1. Backend ↔ Client 契约

### 1.1 通道分工（WS vs REST 路由原则）

后端与客户端同时暴露 JSON-RPC over WebSocket 与 HTTP REST。两套通道的**设计意图不同**——选错通道等于把一类语义错配到不属于它的链路：

| | WS（JSON-RPC） | REST |
|---|---|---|
| **设计意图** | 绑定进程内上下文的持续推送通道——事件、进度与小包数据流以 WS 为单一推送路径，关 WS 即丢弃运行时状态 | URL 寻址、无状态 CRUD——任何持有 JWT 的入口（Hub、CLI、脚本、第三方集成）可独立调用，与 chat 是否在线无关 |
| **承载语义** | 长会话、跨多次往返、需要进程内锚点的小包 | 幂等 CRUD、可独立寻址的对象、多 KB-MB 载荷上传/下载 |

**判别启发（顺序问）**：依赖进程内状态吗 → WS；需要在 chat 未连接时执行吗 → REST；是生产者/进度/状态推送吗 → 通知侧永远经 WS outbox + 事件帧下发（与命令端走哪条无关）。

**REST 镜像特例**：某条 WS 方法的读被一个不持有 WS 连接的 UI 表面（典型为 Hub）需要时，可保留 REST 镜像。镜像必须包装同一个服务函数、两端契约等价，任何漂移都要双端同步。

### 1.2 伙伴生命周期方法（方法级契约）

普通 chat / tool 类方法见 [backend/README.md](backend/README.md)。以下是**伙伴生命周期**专用方法，客户端必须实现消费状态机（详见 [DESIGN.md §5–§6](DESIGN.md)）。**逐参数签名见 backend 代码，本文只锁定契约意图与「改这里要同步哪里」：**

| 方法 | 用途 | 改动需同步的模块 |
|------|------|------------------|
| onboarding.get_state / onboarding.submit 与 GET /api/companion/onboarding/state | 查询/增量提交 onboarding 答案（断点恢复，支持 WS RPC 与 REST） | Backend 状态机 + Client 消费状态机 + DESIGN §5 流程 |
| avatar.regenerate | 重生头像（不使模型失效） | Backend + Client 头像展示 |
| tts.match_voice / tts.design_voice / tts.list_voices | 音色描述匹配 / 专属音色生成 / 目录枚举 | Backend TTS + Client 音色页 + 工具窗口 REST 镜像 |
| companion.set_disturbance_tier | Client 上报生效打扰档位（Client 是唯一权威） | Backend 持久化 + Client 活动感知 + DESIGN §6.2 |
| companion.check_affect / companion.interact / companion.should_act / companion.record_interaction_stats / companion.get_user_profile | 情境化情绪 / 戳反应 / 自主空间决策 / 互动统计 / 画像召回 | Backend 推理 + Client 触发与消费 + DESIGN §6.3/§6.4 |
| POST /api/companion/portrait/confirm | 确认半身形象（幂等），解开全身 3D 风格选择子阶段 | Backend 状态 + Client 流程 |
| GET /api/companion/avatar/fullbody/styles | 查询全身立绘画风目录（日系赛璐珞 / 二次元游戏CG） | Backend 静态目录 + Client 流程 |
| POST /api/companion/avatar/{avatar_id}/fullbody/samples | 并发生成多画风正面全身样图供用户选择锁定（草稿落 temp-media，路径随形象行持久化供断点恢复复用） | Backend 生成 + Client 风格选择卡片 |
| POST /api/companion/avatar/{avatar_id}/fullbody/select-style | 持久化用户选定的画风（不触发生成），重启恢复到正面预览而非重新出样图 | Backend 状态 + Client 流程 |
| POST /api/companion/avatar/{avatar_id}/fullbody/front | 按选定画风与微调反馈生成/重绘正面全身图 | Backend 生成 + Client 正面预览与微调 |
| POST /api/companion/avatar/{avatar_id}/fullbody/back | 按选定画风、正面种子与微调反馈生成/重绘背面全身图（仅在多视角供应商下启用） | Backend 生成 + Client 背面预览与微调 |
| POST /api/companion/avatar/{avatar_id}/fullbody/confirm-front | 确认正面（及可选背面）全身图并解开音色/用户子阶段；后续种子图准备见 [docs/PIPELINE.md §1](docs/PIPELINE.md) | Backend 生成 + Client 流程 |
| GET/POST /api/companion/model | 查询 / 触发 3D 模型异步生成；输入、产物与动画映射契约见 [docs/PIPELINE.md](docs/PIPELINE.md) | Backend 生成管线 + Client 加载 + DESIGN §5.6 |
| companion.model.retryDownload | 仅重试下载已付费的 3D 生成结果，不重新提交生成 | Backend 生成管线 + Client 失败态入口 |
| POST /api/companion/sprite | 静态精灵相册解析（降级渲染源） | Backend 生成 + Client 降级层 + DESIGN §1.2 |
| POST /api/companion/expression-avatar | 表情头像解析（按情绪 token 精确匹配 / 未命中懒生成，身份锚定 active avatar） | Backend 生成 + Client 聊天窗表情头像 + DESIGN §1.1 |
| POST /api/companion/avatar（含 /from-image）、/avatar/{id}/select 与 GET /avatar/history | 半身头像生成（含上传参考图重绘）/ 历史形象切换激活 / 历史查询 | Backend 生成 + Client 头像确认与历史画廊 + DESIGN §5.4 |

**关键约束**（跨模块语义，非实现细节）：
- **断点恢复**：角色子阶段答完即标记角色已定稿；onboarding 整体只在全身形象确认且音色 + 用户信息齐后才算完成；未确认形象时按半身头像 → 全身立绘逐步恢复，确认后按音色先于用户信息路由。全身立绘子阶段的样图与已选画风随形象行持久化，断点恢复直接重放、不重复触发生成；样图草稿确认前停留 temp-media，确认时才转存正式存储，草稿过期按未生成处理由客户端重新生成。
- **形象锁定**：形象确认即锁定，物种/性别/基础外貌不可再改，3D 模型/头像重新生成路径关闭。
- **下载失败可恢复（已付费结果绝不丢）**：下载失败态随 `model.failed` 事件下发可重试标记与模型标识；客户端必须据此提供"重试下载"入口，而非引导重新生成。持久化与恢复语义见 [docs/PIPELINE.md §3](docs/PIPELINE.md)。

### 1.3 事件类型

| 事件 type | 触发时机 | 消费者 |
|-----------|----------|--------|
| companion.affect | 非言语的情境化情绪反应 | Client 切 EMOTIONAL（安静档也透传） |
| avatar.regenerated | 头像重生最终结果 | Client 替换头像或展示失败 |
| model.ready / model.gen.progress / model.failed | 3D 模型就绪 / 进度 / 失败；载荷契约与产物映射见 [docs/PIPELINE.md](docs/PIPELINE.md) | Client 加载与状态展示 |
| companion.assets.updated | 伙伴实时创建了新表情（注册自创情绪并后台生成头像图） | Client 重拉 /expressions（自创情绪注册表：白名单、表情胶囊） |
| video_gen.completed / .failed | 视频生成结果 | 媒体展示 |
| reload.mcp | MCP 配置变更后通知重载 | Client 转发 Runner，重载后回同步工具表 |

**事件投递范围（session_id 语义）**：session_id 就是 conversation_id 的字符串形式（见 §6）。聊天会话事件（message.* / tool.* / error）必带 session_id、只属于该会话，渲染端必须按 session_id 过滤；outbox 事件（上表）不带 session_id，投递到该用户的 desktop、与打开哪个会话无关，照常处理。

### 1.4 Affect 与空间契约

**语义/渲染解耦**——Backend 只产出情绪 + 可选场所语义，绝不指定渲染方式或像素坐标。

**emotion 枚举**（22 项，权威源 backend/services/chat/affect.py）：happy / sad / surprised / excited / confused / concerned / shy / proud / grateful / playful / bored / lonely / sleepy / curious / embarrassed / apologetic / neutral / pout / angry / smug / scared / relieved。

**locale 枚举**（5 项，权威源在后端白名单）：home / chat / perch / roam / sleep。

**spatial target**（可选，仅 perch 时有意义）：窗口/进程名关键字。客户端经窗口枚举解析为窗口几何后计算 perch 点。注意：此处的 target 是空间 cue 的**窗口关键字**，与 Client 内部的场所 target（仪式行走目的地）是**两个不同概念**——后者由工具调用本地触发、不在本协议枚举内（见 [DESIGN.md §3.2](DESIGN.md)）。

**inline 空间 cue 规则**：LLM 在回复前自填空间 cue，由解析器解析后附加到 message.complete 的场所/目标字段。后端解析后下发，客户端决定是否落位（档位门控 + 对话开启抑制）。

**动作 tag（[action:NAME]）**：LLM 可另附一个结构化动作名（snake_case），且只能来自后端注入的可请求动作清单；解析后随对话完成事件下发。清单来源与客户端兑现规则见 [docs/PIPELINE.md §5](docs/PIPELINE.md)。

**表情契约**：自创情绪经工具注册后并入白名单，并按后台生成语义预热头像图；渲染分工见 [DESIGN.md §1.1](DESIGN.md)。

**连续气泡分隔**：LLM 需要在一回合内连发多条短回复时，用单独一行 `---` 分隔；Backend 流式解析为 `message.break` 事件（带 session_id），Client 收尾当前气泡、停顿 0.5–1.5s 后再渲染下一气泡。

**扩展协议**：每次扩展 emotion / locale 须同步更新 **后端白名单 + 客户端表情/场所映射 + 本文档**三处；未覆盖项一律按 neutral / home 处理。情绪枚举 22 项（含 neutral），可生成表情头像 21 项（neutral 即形象头像本身，永不生成）。

### 1.5 资产 URL 签名与传输缓存

| 资产 | TTL |
|------|-----|
| portrait 头像 | 5 分钟 |
| 3D 模型 GLB | 5 分钟 |
| 静态精灵相册 PNG | 5 分钟 |
| 表情头像 PNG | 5 分钟 |

**表情头像缓存键**为 (用户, 情绪 token, 头像)——头像重生后旧行成为陈旧身份、按未命中重新生成；行/文件丢失同样视为未命中（缓存允许丢失，丢失后重生成）。与相册相同的 match-or-generate 语义，但按 token 精确匹配（无 LLM 语义匹配调用）。

**契约要点**：资产端点支持双通道鉴权——已登录 Client 携带有效 Bearer JWT 时可直接访问归属资产；未携带令牌时按 URL HMAC 签名校验（每次签名 5 分钟 TTL，换设备/过期需重新签名）。服务端模型/资产端点支持 HTTP Range 断点续传 + ETag + 不可变缓存头；Client 按内容哈希（SHA-256）在本地磁盘缓存，命中即跳过网络，未命中/中断走断点续传。

### 1.6 错误信封

REST 端点异常路径返回统一结构：error（短码）+ reason（分类，可空）+ status（HTTP 状态）。WS JSON-RPC 错误使用标准错误码（-32700 到 -32603）。**关键契约**：内部错误抛至前端前必须脱敏，严禁包含数据库账号、服务器本地路径等栈帧细节；统一错误分类决定恢复策略，见 [backend/README.md](backend/README.md)；流式 chat 一旦首 chunk 已发，任何供应商失败都不切换 fallback。

---

## 2. Client ↔ Runner 契约

### 2.1 链路与鉴权

- Runner **主动**连客户端提供的 IPC 端点（Windows 命名管道 / macOS UDS，权限 0600）。
- 端点路径与 token 由客户端**单向下发**（启动参数 + 落盘文件）；Runner 重连间重读文件以在客户端重启后拾取新端点与新 token。
- 启动后发 runner_ready 握手通知；鉴权走 upgrade 头，校验失败客户端回 401、不完成握手；Runner 收到 401 后丢弃内存缓存端点与 token、等待重读文件。token 为每次启动新生成的 256-bit 随机值，**不是 Backend 凭据**。
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

capabilities 与 capabilities_health 来源于 Runner 的运行时探测（探测设计见 [runner/README.md §2](runner/README.md)）：前者是向后兼容的布尔映射，后者按子能力给出可用性与失败原因。致命探测异常置 probe_failed；客户端按能力缺失做局部降级或给出可操作提示。

### 2.4 配置推送所有权

**客户端是配置的唯一拥有者**，经 spiritagent.config.update 把完整配置 dict 推送给 Runner。Runner 仅在内存持有配置、每次工具调用读取，不读写磁盘配置文件。时序：Runner 就绪握手后、首个 execute_tool 前推一次 full config；此后每次设置页保存再推一次；Runner 重启后内存配置清空，客户端在下次 runner_ready 时重新推送。客户端把配置以 JSON 存储在用户主目录（非用户面向）。**config schema 的键明细见 runner 代码（utils/config.py），本文只锁定所有权与推送时序契约。**

---

## 3. 反向 RPC 桥接（Runner 借大脑）

**核心约束**：Runner **零凭证运行**，不持有任何后端 Token；所有出站 LLM 请求必须向上借道客户端。本地 IPC 链路的握手 token 只守 Client↔Runner 之间，不是 Backend 凭据，不参与任何 Backend 请求的鉴权。

**链路**：Runner ──(本地 WS: request_llm)──> Client ──(HTTP POST: /api/llm/completion)──> Backend ──> LLM（Client JWT 鉴权）。

**速率守卫**：Client 转发前统计单会话请求次数与载荷大小（硬上限 200 帧 / 1MB），防止 Runner 工具逻辑失控刷爆 LLM 额度。

---

## 4. IPC Future 桥接（Backend 侧契约）

call_id 是 Backend IPC future 字典的**唯一 Future Key**，标识单次 RPC 生命周期。

**键结构**：按 (user_id, call_id) 二元组寻址，而非单 call_id——并发用户不共享 future；user_id 来自 JWT 解析（受保留键保护）；WS 断开时取消该用户所有未决 future。

**超时与快速失败**：默认 300s 超时返回 synthetic error；下发前做连接在线 → 工具可用 → 发送异常三层检查，通常毫秒级返回离线错误，仅绕过三层后才进入超时。

**JWT 过期边界**：token 在飞行途中过期时客户端回传被拒 → future 挂起直到超时；token 过期不触发 WS 断开，当前靠超时兜底。

**通用事件分发**：复用同一 future 通道发任意 JSON-RPC 事件（如 reload.mcp）。

---

## 5. 跨模块安全契约

> 架构原则（物理隔离、防御纵深）见 [ARCHITECTURE.md §7](ARCHITECTURE.md)；本节锁定跨模块**契约**。主动消息的 outbox 下发机制见 [ARCHITECTURE.md §5](ARCHITECTURE.md)，此处不重复。

### 5.1 Reserved Keys（防 LLM 入参注入）

LLM 工具入参**禁止**覆盖保留键：user_id / llm_config / user_settings（后端在工具入口静默丢弃）。**角色定义同等保护**：角色定义作为系统提示词的一部分，同样受此保护，防止用户对话内容注入改写伙伴人格。**新增保留键须在本文档 + 工具入口两处同步。**

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

**职责分立**：session_id 就是 conversation_id 的字符串形式——客户端侧始终用字符串、后端侧持久化为整型，通信边界完成两者转换；call_id 作为唯一 Future Key 标识生命周期（见 §4）。

---

## 7. 跨模块语言规则

LLM 在生成 affect / spatial 时按以下规则：
- affect 的 emotion 必须从 §1.4 枚举集选，LLM 在回复前自填，由解析器解析。
- spatial 的 LOCALE 从 §1.4 locale 枚举选，target 可选、仅 perch 时有意义。
- **后端不产出像素坐标**——客户端据 locale + 当前空间状态决定最终位置与移动方式；target 仅在 perch 时由客户端经窗口枚举解析为窗口几何后计算 perch 点。

---

## 8. 维护规约

- 本文档是**跨模块公共契约**——任何改动必须同时通知所有受影响的模块所有者。
- 任何扩展 emotion / locale / 事件 type，必须在 **本文档 + 后端白名单 + 客户端消费代码**三处同步。
- 任何 Reserved Key 新增，必须在 **本文档 + 工具入口** 同步。
- 子模块 README 不重复本文档内容，只在需要时链接。
