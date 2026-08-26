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
- **全量重水化的防御性截断**：当全量重水化返回的消息数达到防御上限时，响应携带截断标记与更早历史的分页游标；客户端可通过会话消息 REST 端点按游标向后翻页拉取更早历史。该截断仅作为超大历史的负载防御兜底，优先仍走重放缓冲的无缝恢复。
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
| companion.set_timezone | Client 每次连接上报本地 IANA 时区——夜间批处理与互动统计按用户本地日聚合的唯一时区来源；缺行时夜间流水线整段跳过 | Backend 持久化 + Client boot 上报 + DESIGN §6.2 |
| companion.check_affect / companion.interact / companion.should_act / companion.record_interaction_stats / companion.get_user_profile | 情境化情绪 / 戳·摸头·眩晕反应 / 自主空间决策 / 互动统计 / 画像召回 | Backend 推理 + Client 触发与消费 + DESIGN §6.3/§6.4 |
| POST /api/companion/portrait/confirm | 确认半身形象（幂等），解开正面全身立绘生成子阶段 | Backend 状态 + Client 流程 |
| POST /api/companion/avatar/{avatar_id}/fullbody/front | 按默认赛璐珞画风与微调反馈生成/重绘正面全身图 | Backend 生成 + Client 正面预览与微调 |
| POST /api/companion/avatar/{avatar_id}/fullbody/back | 按正面种子与微调反馈生成/重绘背面全身图（3D 升级阶段的背面种子确认向导调用；形象锁定后仍可用——视角派生而非身份变更；风格由系统按类人 CG / 非人写实自动推导） | Backend 生成 + Client 背面预览与微调 |
| POST /api/companion/avatar/{avatar_id}/fullbody/confirm-front | 确认正面全身图并解开音色/用户子阶段（引导期不生成背面种子图，背面准备见 [docs/PIPELINE.md §1](docs/PIPELINE.md)） | Backend 生成 + Client 流程 |
| GET/POST /api/companion/model | 查询 / 触发 3D 模型异步生成；输入、产物与动画映射契约见 [docs/PIPELINE.md](docs/PIPELINE.md) | Backend 生成管线 + Client 加载 + DESIGN §5.5 |
| GET/POST /api/companion/2d | 查询 / 触发 2D 形象生成流水线（see-through 双 provider 拆分，产物恒为分层 PSD）；产物契约见 [docs/PIPELINE.md §6](docs/PIPELINE.md) | Backend 生成管线 + Client puppet 渲染链 |
| POST /api/companion/render-mode | 切换并持久化伙伴渲染模式（`2d` / `3d`） | Backend 持久化 + Client 实时切换 |
| companion.model.retryDownload | 仅重试下载已付费的 3D 生成结果，不重新提交生成 | Backend 生成管线 + Client 失败态入口 |
| POST /api/companion/expression-avatar | 表情头像解析（按情绪 token 精确匹配 / 未命中懒生成，身份锚定 active avatar） | Backend 生成 + Client 聊天窗表情头像 + DESIGN §1.1 |
| POST /api/companion/avatar（含 /from-image、/upload）、/avatar/{id}/select 与 GET /avatar/history | 半身头像生成（含上传参考图重绘、直接上传头像）/ 历史形象切换激活 / 历史查询 | Backend 生成与上传 + Client 头像确认与历史画廊 + DESIGN §5.4 |
| GET/POST /api/companion/outfits 与 POST /{id}/regenerate、/{id}/confirm、PUT /{id}/activate、DELETE /{id} | 2D 换装衣柜：外观列表（首次访问懒合成初始形象）/ 草稿生成（着装描述 + 可选服装参考图，身份恒为正面种子主参考）/ 微调重绘 / 确认转正并触发 2D 切分（failed 可重试切分）/ 即时穿着 / 删除（穿着中与切分中拒绝）。生成走独立小时级频控（默认 1 套/小时），不设数量上限 | Backend 生成管线 + Client 衣柜 + DESIGN §1.1 / §8 |

**关键约束**（跨模块语义，非实现细节）：
- **断点恢复**：角色子阶段答完即标记角色已定稿；onboarding 整体只在全身形象确认且音色 + 用户信息齐后才算完成；未确认形象时按半身头像 → 全身立绘逐步恢复，确认后按音色先于用户信息路由。全身立绘子阶段的已生成正面种子图随形象行持久化，断点恢复直接重放到正面预览；正面种子草稿确认前停留 temp-media，确认时才转存正式存储，草稿过期按未生成处理由客户端重新生成。
- **形象锁定**：形象确认即锁定，物种/性别/基础外貌不可再改，3D 模型/头像重新生成路径与历史头像切换激活一并关闭（切换激活等于换掉已确认的视觉身份）。
- **下载失败可恢复（已付费结果绝不丢）**：下载失败态随 `model.failed` 事件下发可重试标记与模型标识；客户端必须据此提供"重试下载"入口，而非引导重新生成。持久化与恢复语义见 [docs/PIPELINE.md §3](docs/PIPELINE.md)。

### 1.3 事件类型

| 事件 type | 触发时机 | 消费者 |
|-----------|----------|--------|
| companion.affect | 非言语的情境化情绪反应 | Client 切 EMOTIONAL（安静档也透传） |
| avatar.regenerated | 头像重生最终结果 | Client 替换头像或展示失败 |
| model.ready / model.gen.progress / model.failed | 3D 模型就绪 / 进度 / 失败；载荷契约与产物映射见 [docs/PIPELINE.md](docs/PIPELINE.md) | Client 加载与状态展示 |
| companion.2d.ready / .failed | 2D 拆分就绪 / 失败；载荷包含 manifest_url 与图层签名 URL 字典。manifest 恒为分层 PSD 描述符（`kind=psd`，产物契约见 docs/PIPELINE.md §6.1） | Client 水合 puppet 渲染路径 |
| companion.render_mode.changed | 用户在设置中或多端同步切换渲染模式（`2d` / `3d`） | Client 切换展示画布 |
| companion.assets.updated | 伙伴实时创建了新表情（注册自创情绪并后台生成头像图） | Client 重拉 /expressions（自创情绪注册表：白名单、表情胶囊） |
| companion.outfit.updated / .failed | 换装外观状态变化（切分就绪 / 穿着翻转 / 删除，载荷含 outfit_id 与 worn 标记）/ 切分失败（含原因） | Client 重拉衣柜列表；worn 变化时重水合 2D 渲染层（与 2d.ready 双触发幂等，事件只当刷新触发、列表端点是真相源） |
| video_gen.completed / .failed | 视频生成结果 | 媒体展示 |

**事件投递范围（session_id 语义）**：session_id 就是 conversation_id 的字符串形式（见 §6）。聊天会话事件（message.* / tool.* / error）必带 session_id、只属于该会话，渲染端必须按 session_id 过滤；outbox 事件（上表）不带 session_id，投递到该用户的 desktop、与打开哪个会话无关，照常处理。

### 1.4 Affect 与空间契约

**语义/渲染解耦**——Backend 只产出情绪 + 可选场所语义，绝不指定渲染方式或像素坐标。

**emotion 枚举**（22 项，权威源 backend/services/chat/affect.py）：happy / sad / surprised / excited / confused / concerned / shy / proud / grateful / playful / bored / lonely / sleepy / curious / embarrassed / apologetic / neutral / pout / angry / smug / scared / relieved。

**locale 枚举**（3 项，权威源在后端白名单）：home / perch / roam。

**spatial target**（可选，仅 perch 时有意义）：窗口/进程名关键字。客户端经窗口枚举解析为窗口几何后计算 perch 点。注意：此处的 target 是空间 cue 的**窗口关键字**，与 Client 内部的场所 target（仪式行走目的地）是**两个不同概念**——后者由工具调用本地触发、不在本协议枚举内（见 [DESIGN.md §3.2](DESIGN.md)）。

**inline 空间 cue 规则**：LLM 在回复前自填空间 cue，由解析器解析后附加到 message.complete 的场所/目标字段。后端解析后下发，客户端决定是否落位（档位门控 + 对话开启抑制）。

**动作 tag（[action:NAME]）**：LLM 可另附最多 3 个结构化动作名（snake_case，各占一行、按播放顺序排列），且只能来自后端注入的可请求动作清单——白名单外与超额的 tag 在后端流式解析时丢弃；解析后以 `affect.actions` 数组随对话完成事件下发（2D 依序播放，3D 取首个）。清单来源与客户端兑现规则见 [docs/PIPELINE.md §5–§6](docs/PIPELINE.md)。

**action 白名单（2D 路径）**（权威源 [backend/services/companion/mesh2d/actions.py](backend/services/companion/mesh2d/actions.py) 的 `DEFAULT_ACTIONS`，LLM 注入清单为 `DEFAULT_ACTIONS − NON_LLM_ACTIONS`；下表标 ★ 的键为客户端本地触发、LLM 不可请求）：

| key | 描述 | 默认绑定 emotion |
|---|---|---|
| `wave_right` / `wave_left` | 单手举起挥手 | happy / excited / 告别 |
| `present_right` / `present_left` | 单手抬起展示 / 指向 / 拿东西 | helpful（"帮我拿杯子"） |
| `point_right` / `point_left` | 抬臂指向屏幕目标（仪式行走抵达后按方位播放） | helpful / curious（"看这个"） |
| `hands_on_hip` | 双手叉腰 | smug / pout / proud |
| `hair_touch` | 抬手整理头发（带轻挠关键帧） | shy / thinking（害羞拨发） |
| `spread_arms` | 双臂展开做展示 | happy / excited / proud |
| `look_away_left` / `look_away_right` | 头脸避开视线 | shy / embarrassed / sad |
| `turn_body_left` / `turn_body_right` | 整个上半身转向 | 切换朝向 / 仪式行走 |
| `lean_forward` | 上半身微微前倾 | curious / thinking |
| `shy` | 低头侧脸 + 前发微盖 | shy / embarrassed |
| `petting` | 享受抚摸：微微歪头闭眼 + 舒服蹭蹭 | happy / grateful（摸头手势触发） |
| `dizzy` | 眩晕：脑袋发懵轻晃 + 圈圈眼 | confused / tired（狂戳/狂甩触发） |
| ★ `fall` | 自由落体悬空下落姿态 | scared / surprised（空中释放触发） |
| ★ `land_squash` | 触地弹性挤压扁平瞬间变形 | neutral（落地瞬间触发） |
| ★ `peeking` | 贴边探头偷看姿态 | curious / playful（屏幕贴边吸附触发） |
| `idle_glance` | 短瞥一眼回中 | idle 变体 |
| ★ `click` | 伸手触碰 / 点击姿态 | neutral（仪式行走飞抵目标触发） |
| ★ `long_press` | 长按凝视姿态 | neutral（用户长按精灵触发） |
| ★ `drag_end` | 拖拽释放落地的站稳微沉 | neutral（拖拽放下触发） |

> 注：3D 路径走 GLB clip map；2D 路径走 [PuppetStage](client/renderer/companion/puppet/PuppetStage.tsx) 定时包络（白名单键同源，通道由包络内部定义）。同一 action key 在各路径上语义一致但兑现方式不同。
>
> 走路 / 跳跃 / 下落（locomotion）：2D 路径下由空间层驱动位置移动、发束/裙摆次级物理自然反馈；抛掷 / 重力落体的姿态反馈尚未接入 puppet。如需移动角色，用 spatial cue / ritual walk 而非 action。

**表情契约**：自创情绪经工具注册后并入白名单，并按后台生成语义预热头像图；渲染分工见 [DESIGN.md §1.1](DESIGN.md)。

**连续气泡分隔**：LLM 需要在一回合内连发多条短回复时，用单独一行 `---` 分隔；Backend 流式解析为 `message.break` 事件（带 session_id）并**自行控制 0.5–1.5s 的分段节流**——停顿在后端流内完成，Client 按帧到达顺序收尾当前气泡再渲染下一气泡，双端无需各自计时。

**2D 命中区域与手势交互协议**：
- `companion.interact` RPC payload 的 `kind` 字段支持 `poke`（戳击）、`pet`（摸头抚摸）、`dizzy`（激怒/眩晕）。
- `companion.interact` RPC payload 的 `region` 字段允许传下列白名单之一（不传 = 整精灵矩形命中）：

| region | 含义 |
|---|---|
| `head` | 头部（含 face） |
| `face` | 脸部（head 子区域） |
| `arm_L` / `arm_R` | 左 / 右手臂 |
| `body` | 躯干 |
| `back_hair` / `front_hair` | 后发 / 前发 |
| `skirt` | 下装 / 裙子 |

命中区域与手势影响：（1）前端手势/物理反馈——head/face 往复滑动触发摸头享受姿态（`petting` 眯眼）与爱心粒子（💖）；连戳 ≥ 5 次冒怒气（💢），≥ 8 次或剧烈狂甩触发眩晕（`dizzy` 星环 💫）；空中释放触发重力落体与落地挤压反弹（`land_squash`）；hover 头发区域触发前/后发 jiggle 抖动；（2）LLM 反应上下文——`kind` 与 `region` 字段透传到 LLM，让回应可针对"摸头" vs "戳脸" vs "拍手" vs "摇晃眩晕"做不同文案。3D 路径走 silhouette hit（pixel-perfect alpha 检测）；2D 路径走 [PuppetStage 六区](client/renderer/companion/puppet/PuppetStage.tsx)（rig 锚点 / 层矩形 bbox 测试，CPU 轻量，经命中区域总线 `$mesh2dHitmap` 下发）。

**扩展协议**：每次扩展 emotion / locale 须同步更新 **后端白名单 + 客户端表情/场所映射 + 本文档**三处；未覆盖项一律按 neutral / home 处理（2D puppet 链的情绪→面部参数映射随词表同步）。情绪枚举 22 项（含 neutral），可生成表情头像 21 项（neutral 即形象头像本身，永不生成）。action 扩展须同步更新 **后端 [actions.py](backend/services/companion/mesh2d/actions.py)（DEFAULT_ACTIONS / NON_LLM_ACTIONS）+ 客户端 [PuppetStage 包络表](client/renderer/companion/puppet/PuppetStage.tsx) + 本文档**三处。

### 1.5 资产 URL 签名与传输缓存

| 资产 | TTL |
|------|-----|
| portrait 头像 | 5 分钟 |
| 3D 模型 GLB | 5 分钟 |
| 2D 部件 PNG / manifest.json | 5 分钟 |
| 2D 分层 PSD（分层切分产物，puppet 链消费） | 5 分钟 |
| 表情头像 PNG | 5 分钟 |
| 换装外观全身立绘（草稿期为 temp-media 免鉴权路径，确认后转正式签名） | 5 分钟 |

**表情头像缓存键**为 (用户, 情绪 token, 头像)——头像重生后旧行成为陈旧身份、按未命中重新生成；行/文件丢失同样视为未命中（缓存允许丢失，丢失后重生成）。match-or-generate 语义：命中缓存行即返签名 URL，未命中才生成；按 token 精确匹配（无 LLM 语义匹配调用）。

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
| runner_ready | Runner → Client | 启动握手，携带 version + capabilities + capabilities_health + reconnect_streak | Runner 探测 + Client 功能门控 + 重连降级展示 |
| tools_changed | Runner → Client | 工具 schema 变更通知，Client 重拉并同步到 Backend | Runner + Client + Backend 工具表 |
| get_tools | Client → Runner | 获取工具 schema（已过滤禁用项） | Runner 过滤 + Client + Backend |
| spiritagent.info | Client → Runner | 完整运行快照 | Runner 上报 + Client 诊断 |
| execute_tool | Client → Runner | 执行工具调用 | Backend 路由 + Client 中转 + Runner 执行 |
| spiritagent.cancel | Client → Runner | params.req_id 可选；指定则取消该 RPC，缺省取消当前进行中工具；并对目标 req_id 设置中断标记 | Client 中断 + Runner 任务取消 + 请求级隔离 |
| spiritagent.config.update | Client → Runner | 推送完整配置（Client 是唯一拥有者） | Client 设置 + Runner 内存配置 |
| request_llm | Runner → Client | 反向 RPC 借大脑 | §3 |

### 2.3 runner_ready capabilities 与 health 状态

capabilities 与 capabilities_health 来源于 Runner 的运行时探测（探测设计见 [runner/README.md §2](runner/README.md)）：前者是向后兼容的布尔映射，后者按子能力给出可用性与失败原因。致命探测异常置 probe_failed；客户端按能力缺失做局部降级或给出可操作提示。

`reconnect_streak` 是自上次成功握手以来的连续重连次数（握手成功后重置为 0）。客户端可据此感知连接状态但保持 Runner 存活。生命周期累计重连计数通过 `spiritagent.info.reconnect_count` 上报，不重置。

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

---

## 5. 跨模块安全契约

> 架构原则（物理隔离、防御纵深）见 [ARCHITECTURE.md §7](ARCHITECTURE.md)；本节锁定跨模块**契约**。主动消息的 outbox 下发机制见 [ARCHITECTURE.md §5](ARCHITECTURE.md)，此处不重复。

### 5.1 Reserved Keys（防 LLM 入参注入）

LLM 工具入参**禁止**覆盖保留键：user_id / llm_config / user_settings（后端在工具入口静默丢弃）。**角色定义同等保护**：角色定义作为系统提示词的一部分，同样受此保护，防止用户对话内容注入改写伙伴人格。**新增保留键须在本文档 + 工具入口两处同步。**

### 5.2 不可信工具结果包裹

外部（Web 搜索 / 浏览器抓取）获取的字符串注入 LLM 上下文前强制包裹。短字符串不包——注入风险低 + 节省 token。

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
