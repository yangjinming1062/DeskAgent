# DeskAgent 架构设计

> **面向人类与 Agent 的系统总览。读代码前先读本文。**

DeskAgent 是一个**根据用户描述定制的、具有专属形象的陪伴型桌面伙伴**。用户首次安装后经一段 onboarding 对话定制伙伴的名字、性格、说话风格与外貌，系统据此生成专属桌面形象；此后伙伴常驻桌面、主动陪伴，并调用本机能力帮用户做事。产品体验的完整设计（形象、动画、生命周期、onboarding、交互范式）见 [COMPANION_DESIGN.md](COMPANION_DESIGN.md)；本文聚焦系统物理架构——模块边界、通信协议、安全防线、跨模块不变量。

三个关键词决定一切产品与技术取舍：**定制**（形象与人格由用户定义并生成）、**陪伴**（主动、持续、有记忆的关系，而非一次性问答）、**伙伴**（交互对象是"他/她/它"，工具能力只是伙伴"会做的事"，不是产品的主角）。

---

## 1. 三模块物理解耦

系统在物理上拆分为三个核心子模块，各自承载不同职责、运行在不同环境、由不同信任域管辖：

| 层 | 所在 | 承载 | 为什么必须在物理上分离 |
|----|------|------|------------------------|
| **大脑** | Backend（云端） | LLM 编排、伙伴人格（角色定义 + 长期记忆）、形象资产生成、云端工具 | 伙伴的"性格与记忆"是跨设备、需多用户隔离、且依赖大模型与生图算力的核心资产，必须云端托管 |
| **手脚** | Runner（本地） | 终端、文件、浏览器、代码沙箱等本机操作能力 | 伙伴"帮用户做事"必须真正触碰用户本机；但本机操作不可上云，且执行环境必须与凭证隔离 |
| **形象与枢纽** | Desktop（本地） | 桌面精灵形象渲染、陪伴式交互、凭证持有、WS 中转、Runner 生命周期 | 伙伴要在用户桌面常驻可见、能听能说；同时是唯一可信的凭证持有点与本地/云端流量中转点 |

**核心不变量**：云端永不接触用户本机与凭证，本地 Runner 零凭证运行，Desktop 是唯一的可信枢纽。即便伙伴人格被 prompt 注入攻陷，最坏情况也只是受限用户账户下的本地工具被调用，不会泄露云端凭证或影响其他用户。

伙伴的"人格"由云端持久化的**角色定义**驱动（注入每次对话的系统提示词）；伙伴的"形象"由云端生成的**形象资产**驱动（由 Desktop 拉取并渲染）。两者共同构成"这一个伙伴"的数字身份，归属于该用户。

---

## 2. 架构拓扑 (Topology)

系统在物理上拆分为三个核心子模块与一个独立的安装器引导组件：

```
┌─────────────────────────────────────────────────────────────┐
│                        Backend                              │
│                   (云端大脑 / FastAPI)                        │
│  - 伙伴人格：角色定义持久化、长期/短期记忆管理                │
│  - 专属形象资产生成（图片生成工具）与下发                     │
│  - LLM 编排与系统提示词装配（角色定义注入每次对话）           │
│  - 云端工具执行（Web 搜索、TTS、主动消息 send_message…）      │
│  - 事件发布 / Cron 调度中转（Outbox，主动陪伴的基石）        │
└─────────────────────────┬───────────────────────────────────┘
                          │ WebSocket 长连接 (带用户 JWT 鉴权)
                          │ /api/chat/ws?token=<jwt>
┌─────────────────────────▼───────────────────────────────────┐
│                       Desktop                               │
│              (桌面伙伴形象载体 + 本地枢纽 / Electron)          │
│  - 桌面精灵形象渲染（透明置顶窗口）与陪伴式交互 UI            │
│  - onboarding：角色定义引导、形象资产拉取与孵化过渡           │
│  - 登录鉴权与用户凭证加密落盘 (safeStorage)                   │
│  - 本地 WebSocket 服务端与 Runner 进程生命周期管理            │
│  - 双向工具调用路由及反向 RPC (Reverse RPC) 代理中转          │
│  - 统一自更新（Electron 二进制 + Runner wheel）               │
└─────────────────────────┬───────────────────────────────────┘
                          │ 本地 WebSocket (动态高位端口)
                          │ ws://127.0.0.1:<port>/rpc
┌─────────────────────────▼───────────────────────────────────┐
│                        Runner                               │
│            (本地手脚 / uv build wheel)                       │
│  - 本地环境执行器（PTY 终端、文件读写、代码沙箱）            │
│  - 动态 MCP 协议客户端（加载本地 config.yaml）              │
│  - 浏览器多后端控制（Playwright / Camofox）                 │
│  - 自动化安全审查拦截器（Tirith 扫描 / 写白名单防护）        │
└─────────────────────────────────────────────────────────────┘
```

并行的 **Installer (安装器 / Tauri 2)** 在首次安装时于本地引导 uv Python 3.13 运行时，解压释放 Runner wheel、默认配置与基础技能（Skills）到本地平台标准路径下；首装完成即进入"蛋"阶段。

> Desktop 的定位是**双层叠加**：底层是可信枢纽（凭证、中转、Runner 编排、自更新——这部分是 backend/runner 复用所依赖的不变契约），上层是桌面伙伴形象与陪伴交互。两层共享同一个 Electron 主进程，但职责严格分离：枢纽层处理协议与安全，伙伴层处理形象渲染与用户体验。

---

## 3. 核心模块与职责矩阵 (Responsibilities)

| 维度 | Backend (云端大脑) | Desktop (伙伴载体 + 本地枢纽) | Runner (本地手脚) |
|---|---|---|---|
| **运行环境** | 容器/云端服务器 (Linux Docker) | 用户本机原生环境 (Win/Mac/Linux) | 本地静默进程 (venv Python 运行时) |
| **状态持有** | 伙伴角色定义、形象资产、长期记忆、PostgreSQL、LLM API 凭证、用户会话历史 | 用户身份 JWT (加密)、Runner 进程 PID、本地工具集 Schema 缓存、伙伴形象资产本地缓存 | 终端环境快照、CDP 浏览器会话、本地 MCP Server 句柄 |
| **核心职责** | 接收用户消息、装配角色定义+记忆上下文、流式调度 LLM、按需生成/再生形象资产、解析工具调用并路由 | 渲染桌面伙伴形象、承载陪伴式交互、引导 onboarding；维护本地安全防线、中转 WS 工具帧、代理 Runner 的反向 LLM 请求 | 纯粹执行底层工具逻辑，上报真实可用的工具 Schema 列表 |
| **安全准则** | `<untrusted_tool_result>` 包裹一切外部输入；Reserved 键覆盖保护；脱敏日志；角色定义属用户隐私数据 | Renderer 进程隐藏 JWT；仅暴露本地文件系统代理拦截 | Hardline 危险命令阻断；Windows 路径不敏感写限制；SSRF 防护 |

---

## 4. 通信与消息协议 (Protocols & Flows)

全链路采用 **JSON-RPC 2.0** 封装双向流量。这一协议层是 backend/runner 复用的核心契约。

### 4.1 协议链路映射

#### A. Backend ↔ Desktop
单一 WebSocket 通道 `/api/chat/ws?token=<jwt>`，承载所有流式对话、控制事件、工具触发，以及形象资产下发与 onboarding 控制信令。
- **请求信封 (Request)**: `{"jsonrpc": "2.0", "id": "<call_id>", "method": "<method>", "params": {...}}`
- **事件推送 (Notification)**: `{"jsonrpc": "2.0", "method": "event", "params": {"type": "<event_type>", "payload": {...}}`

**伙伴层协议扩展**（onboarding / 形象与动画资产 / 情绪表达）：在上述信封基础上新增以下方法与事件，承载伙伴生命周期的控制信令。Desktop 侧的消费状态机见 [COMPANION_DESIGN.md §2](COMPANION_DESIGN.md)。

| 方向 | 方法 / 事件 type | 用途 |
|------|------------------|------|
| Desktop → Backend | `onboarding.get_state` | 查询已采集字段与下一步（断点恢复） |
| Desktop → Backend | `onboarding.submit` `{field, value}` | 逐字段增量持久化 onboarding 答案 |
| Desktop → Backend | `avatar.regenerate` `{feedback?}` | 重生 portrait，所有衍生 clip 失效并重排队列 |
| Desktop → Backend | `avatar.list_clips` | 查询 clip 目录与生成状态（就绪/排队/失败） |
| Backend → Desktop | `event.type="companion.affect"` `{emotion}` | affect-only 情绪 cue（无消息文本、无 TTS），驱动 EMOTIONAL 状态——quiet 档透传或 idle 触发 LLM 推理产出（详见 §4.2.IV / §5 / §6.3） |
| Backend → Desktop | `event.type="avatar.regenerated"` `{job_id, asset_url?, id?, error?}` | `avatar.regenerate` 的最终结果通知（含成功 / 失败 payload），Desktop 据此替换 portrait 或展示失败提示；旧衍生 clip 在新 portrait 成功后才失效 |
| Desktop → Backend | `companion.set_disturbance_tier` `{tier}` | 上报当前打扰档位（积极主动/常规/保持安静），约束 Backend 主动消息 |
| Desktop → Backend | `companion.check_affect` `{idle_seconds, local_hour}` | idle 触发的情境化 affect 推理：Backend 加载 persona + 记忆跑一次 LLM 推理，决定是否 emit `companion.affect`（详见 §5 / §6.4） |
| Desktop → Backend | `companion.interact` `{kind: 'poke'\|'drag', tone, poke_count, idle_seconds, local_hour}` | 单次戳/拖的 LLM 反应推理：返回 `{text, emotion, reason}`。per-user inflight 取消 + 1.5s 节流。零延迟本地文案池由 Desktop 自管，LLM 响应仅作文本/情绪增强，不打断本地 TTS（详见 §6.3）。 |
| Desktop → Backend | `companion.record_interaction_stats` `{kind: 'poke'\|'drag'\|'chat_turn', hour}` | 互动统计上报，无 LLM。Backend 按 UTC 自然日聚合三类计数 + 24h hour_buckets；当 poke、drag、chat_turn 三者**各自** ≥ 10 时 upsert `Memory(context="interaction_stats:<date>", content="<date>: poke=N, drag=N, chat_turns=N; peak=HH-HHh", tags=["interaction","stats","daily_summary"])`；同日多次跨门限同 row 覆盖。 |
| Desktop → Backend | `companion.get_user_profile` | 拉取 `Memory(context="user_profile:*")` 的 5 条结构化字段——persona-retune wizard 第 5 步预填用 |

clip 的就绪/失败通知走**单一 `clip.updated` 通道**。companion 服务以 portrait 为种子经图生视频生成 clip，复用 `media/video_jobs` 流水线（通过 `enqueue_video_job(..., emit_event=False)` 抑制标准 `video_gen.*` 事件，避免双通知与字段名错位）。Desktop 另调 `avatar.list_clips` 查询整批 clip 目录与各自生成状态。

#### B. Desktop ↔ Runner
本地环回 WebSocket `ws://127.0.0.1:<port>/rpc`。Desktop 充当 RPC Server，Runner 启动时作为 Client 主动连入。
- 选用 WebSocket 而非 stdio 重定向的权衡：
  1. 避免 C 库底层日志或 Python `print` 污染标准输入输出帧。
  2. 全双工并发，按 `id` 异步匹配响应，避免进程读写阻塞与全局锁。

### 4.2 核心数据流

#### I. 标准工具调用流（伙伴"帮忙"的底层通道）
当云端 LLM 决定调用本地工具（如用户要伙伴整理某个文件夹，触发 `terminal` 或 `write_file`）：

```
LLM (Generate tool_call)
    │
    ▼ (Stream output)
Backend [services/chat/orchestrator.py]
    │  1. 拦截 reserved 字段、检查 iteration_budget
    │  2. 创建 ipc.create_future(user_id, call_id)
    ▼ (WebSocket Push: method="event", type="tool.call")
Desktop [runner-bridge.cjs]
    │  3. 路由分发，由 IPC Bridge 转换帧
    ▼ (Local WS: method="execute_tool", params={"name", "args"})
Runner [server.py]
    │  4. Tirith/SSRF 扫描与文件写保护校验
    │  5. 本地执行（PTY/Playwright 等），清理控制字符与脱敏
    ▼ (Local WS Response: result={"stdout", "exit_code"})
Desktop [runner-rpc-ws.cjs]
    │  6. 捕获连接异常，Runner 离线快速 fail-fast
    ▼ (WebSocket Request: method="tool.result", params={"call_id", "result"})
Backend [api/v1/chat.py]
    │  7. 匹配 ipc.resolve_future(user_id, call_id) 唤醒 chat_service 协程
    ▼
LLM (Receive tool result & continue)
```

> 产品不变量：工具调用在用户侧以"伙伴在帮忙"的叙事呈现，原始 `tool.call` / `tool.result` 帧不直接暴露给最终用户（开发者视图除外）。

#### II. 反向 RPC 流（Runner 借大脑）
本地 Runner 工具（如 `vision_analyze` 或动态 MCP）需要借助大模型能力，但**不持有任何云端凭证/Token**，通过 Desktop 代理转发：

```
Runner ──(Local WS: method="request_llm")──> Desktop ──(HTTP POST: /api/llm/completion)──> Backend ──> LLM
                                               │
                                       (Desktop JWT 鉴权)
```
- **速率守卫**：Desktop 转发前统计单会话请求次数与载荷大小（硬上限 200 帧 / 1MB），防止 Runner 工具逻辑失控刷爆 LLM 额度。

#### III. 本地文件系统代理拦截 (Local Intercepts)
受限于沙箱或 Docker 容器无法看到的本地环境信息，Desktop 在 Renderer 发出 WS 请求前先行拦截处理：
- **`config.get({key: "project"})`**：Desktop 本地直接读取 `.git/HEAD` 恢复分支状态，不上报云端。
- **`complete.path({word, cwd})`**：`@` 路径补全时拦截，Desktop 主进程调用本地文件目录列表。

#### IV. 伙伴表达事件流（情绪 + 动画资产）

伙伴"如何表达"由 Backend 产出语义、Desktop 负责渲染，两者经 WS 解耦。情绪 cue 与动画资产是两条独立的流，互不阻塞：

**affect 流（情绪随话语同行）：**

```
对话响应 / send_message 主动消息
    │  响应帧内联 affect: {emotion} 字段
    ▼
Desktop 状态机：EMOTIONAL(affect) → SPEAKING(TTS) → IDLE
    │  affect 缺失或对应 clip 未就绪 → 回退 SPEAKING(generic) → IDLE
```

**clip 流（动画资产后台异步）：**

```
Backend clip 生成队列（portrait 种子图 + 场景文本 → 图生视频）──event: clip.updated──> Desktop 本地缓存 + 状态机绑定
    │                                          │
    │  (Desktop 调 avatar.list_clips 查进度)    └─ clip 缺失时该状态回退 idle loop
    └── portrait 重生 → 所有衍生 clip 失效重排队列
```

- **inline affect 原则**：情绪 cue 随其所属话语在同一响应帧（`message.complete`）下发，Desktop 无需二次猜测"这句话该配什么情绪"。独立的 `companion.affect` 事件用于非言语的情境化情绪反应——两条路径产出它：(1) `send_message_tool` 在 `quiet` 档下消息被吞但 affect 透传；(2) `companion.check_affect` 的 idle 触发 LLM 推理（详见 §5）。
- **语义/渲染解耦**：Backend 只产出 `emotion` 语义，绝不指定 clip 文件名或渲染方式；Desktop 据本地可用资产决定如何渲染。这条解耦使 clip 的渐进生成不影响语义层（详见 §6.3）。

---

## 5. 事件与 Cron 调度（主动陪伴的基石）

**陪伴区别于工具的关键，在于伙伴会主动找用户。** 系统以 PostgreSQL LISTEN/NOTIFY + Outbox 表支撑毫秒级、可水平扩展的事件分发，使伙伴的主动问候、定时提醒、情境化闲聊无需轮询即可送达：

1. **写出事件**：业务或 Cron 协程将待发送的 WS 帧写入 `ws_events` 表。
2. **数据库触发器**：PostgreSQL 在 `ws_events` 上的 STATEMENT 级触发器写入时自动发出 `NOTIFY ws_events_channel`。
3. **副本认领（Atomic Claim）**：每个 Backend 副本独立 `LISTEN`，收到唤醒后执行 `DELETE ... RETURNING`；行锁保证仅一台副本原子获取并消费该行。调度器 tick 只写入事件不 await WS 发射，避免慢客户端拖垮事务。

`send_message`（主动消息）、Cron（定时任务）、形象/角色变更通知等所有"伙伴主动行为"都经此通道下发至 Desktop。调度循环与 LISTEN/NOTIFY 消费实现见 [backend/README.md](backend/README.md)。

**打扰档位约束**：所有"伙伴主动行为"受三档打扰等级约束（积极主动 / 常规 / 保持安静），档位由用户设置 + Desktop 检测到的用户活动共同决定，Desktop 经 `companion.set_disturbance_tier` 上报。

档位状态分两层（`desktop/renderer/companion/companion-store.ts`）：
- `$userPreferredTier`：用户手动选择，源真值，持久化在 `localStorage`。
- `$effectiveTierOverride`：活动感知器写入的临时覆盖，null 表示无覆盖。
- `$effectiveTier = $effectiveTierOverride ?? $userPreferredTier`：其余模块读取这一原子做静默判定。

Desktop 是档位的唯一权威：它持有用户偏好 + 活动上下文，独立计算 effective 值（`activity.ts::computeLocalEffectiveTier` 应用「手动 quiet 永远不被覆盖」+ immersive/fullscreen → quiet 规则），并通过 `companion.set_disturbance_tier` 把最终结果单向推给 backend。Backend 只在 `_disturbance[user_id]` 字典里镜像这个值供 server-side gate（`send_message_tool`、`cron._kick_autonomous_turn`）使用——既不独立推导，也不存储 user_preferred 或 focus_context。

Backend 据此放行或抑制：`quiet` 档时主动消息被吞掉，但 LLM 推理出的 affect 仍经独立的 `companion.affect` 事件流出——即**断消息不断 affect**，Desktop 收到后切 EMOTIONAL 状态但不弹气泡、不做 TTS。档位的交互表现见 [COMPANION_DESIGN.md §5.2](COMPANION_DESIGN.md)；Backend 门控实现见 [backend/README.md](backend/README.md)。

**情境化 affect（无 turn 触发）**：用户长时间无活动时，Desktop 的 idle 轮询跨过阈值时调 `companion.check_affect {idle_seconds, local_hour}`，Backend 加载 persona + 最近记忆跑一次 LLM 推理，决定是否 emit `companion.affect`。**触发时机由 Desktop 控制（知道真实 idle 状态），情绪推理由 Backend LLM 承担（有 persona + 记忆）**——各取所长，Desktop 不退化成规则驱动的文案池。这条路径是 §6.4"记忆驱动运行时行为"不变量的落地。

**已知限制（多副本）**：`disturbance_tier` 与 IPC future 均为 process-local，多副本部署下用户必须连到同一副本；多副本横向扩展需先将这两处迁至共享存储（Redis 等）。当前设计假设单副本语义。

---

## 6. 角色定义与表达层契约

Backend/Desktop 之间的核心契约：角色定义如何驱动伙伴、形象资产如何生成与下发、情绪如何表达。实现细节（schema、存储表、渲染管线）留给 [backend/README.md](backend/README.md) 与 [desktop/README.md](desktop/README.md)，本文只锁定跨模块的设计意图与不变量。

### 6.1 角色定义（唯一真相源）

onboarding 产出的结构化角色定义持久化在 Backend 用户维度，作为系统提示词的一部分注入该用户的每次对话，驱动伙伴的说话风格、性格表现与主动行为倾向。角色定义是伙伴行为的**唯一真相源**：变更只能由用户显式发起（重新进入角色编辑），禁止 LLM 自行改写。

### 6.2 形象资产（跨模块契约）

伙伴的视觉表达由 portrait、loop clip、transition clip 三层资产构成，均归属用户、在用户维度持久化。资产体系的形态、用途、渲染约束与三档降级设计见 [COMPANION_DESIGN.md §1](COMPANION_DESIGN.md)；此处只锁定跨模块契约：

- **portrait 是全部 clip 的生成种子**：所有 clip 经图生视频（portrait 为种子图 + 场景/动作文本，复用 `media/video_jobs` 流水线）产出。同一颗种子图从机制上保证跨 clip 的角色一致性，无需额外的风格锁。
- **衍生失效**：portrait 重生（`avatar.regenerate`）时所有 clip 必然失配，须全部失效并从新种子重新排队，绝不跨 portrait 版本复用。
- **资产 URL 5 分钟 HMAC 签名**：portrait 与 clip 产物落持久目录（`companion-avatars/` / `companion-assets/`），对外通过 `/api/companion/avatar/file/...?signature=...` 短 TTL 签名 URL 暴露（`signed_url_expiry_seconds=300`）——换设备登录需重新生成签名，不能直接分享原 URL。Desktop 收到后仍应本地缓存避免重复拉取。Backend 的 `verify_signed_asset_request` 强制校验签名，丢了就 401。
- **受控再生成**：形象在多次会话间保持稳定，构成伙伴的视觉身份。变更只在用户主动要求时发生，走 Backend 主导的再生成流程并同步更新 Desktop 本地缓存。
- **渐进式生成**：portrait + idle clip 在 onboarding 同步生成（批次 0）；其余 clip 按优先级后台排队，就绪后经 `clip.updated` 事件下发。分批策略与降级细节见 [COMPANION_DESIGN.md §1.3 / §1.4](COMPANION_DESIGN.md)。

### 6.3 表达层契约

伙伴"说什么"由 LLM 产出，"怎么动、什么情绪"由 Desktop 渲染。以下是两者之间的语义契约：

- **情绪 cue（affect）**：Backend 在对话响应/主动消息中携带 `affect: {emotion}` 语义字段，Desktop 据此驱动动画状态机。emotion 为有限枚举集（`happy / sad / surprised / excited / confused / concerned / shy / proud / grateful / playful / bored / lonely / sleepy / curious / embarrassed / apologetic + neutral`，共 17 项），与 `services/chat/affect.py::ALLOWED_EMOTIONS` 完全对齐。可扩展——但每次扩展须同步 Backend 产出 allowlist 与 Desktop clip 目录，否则未覆盖的 emotion 一律按 `neutral` 处理。
- **语义与渲染解耦**：Backend 只产出 emotion 语义，绝不指定 clip 文件或渲染方式。Desktop 据本地可用资产决定渲染——有对应 clip 则播放，否则回退 idle loop + 状态轻量提示。这使 clip 渐进生成与语义层互不阻塞。
- **affect 继承角色定义的抗注入保证**：affect 由已注入角色定义的 LLM 产出，自然符合人格；角色定义本身受 §7.2 reserved 键与不可信包围机制保护，affect 因此继承同一保证，无需额外的情绪过滤层。
- **affect 与 text 同帧，TTS 由 Desktop 拉取式合成**：对话响应的 `message.complete` 帧内联 `{text, affect}`，不内联 TTS 音频。Desktop 收到后先据 affect 切 EMOTIONAL，再据 text 拉 TTS（`POST /api/media/tts`）切 SPEAKING，保证情绪基调先于语音进入。Backend 只产出语义（emotion + text），TTS 合成归 Desktop 渲染层。
- **user_profile 自动召回**：5 条结构化用户字段（称呼/性别/年龄段/爱好/自由文本）在 chat 入口渲染成 markdown 片段注入 system prompt stable 段，紧跟角色定义之后。LLM 每个 turn 都能直接看到用户身份事实，不依赖 `memory_recall` 工具调用——§6.4 双层模型中"记忆驱动行为"在结构化字段上不靠 LLM 是否记得调工具来决定。

### 6.4 行为驱动的双层模型

伙伴"做什么、怎么做"由两个独立来源驱动，两者不可混淆、也不可错配：

- **角色定义（静态，用户定义）→ 视觉身份与资产生成**：portrait 与所有 clip 的生图/生视频 prompt 从角色定义装配（§6.2）。换风格 = 改角色定义 → 资产重生。这是伙伴"长什么样、动作风格如何"的唯一来源。
- **记忆（动态，随互动累积）→ 运行时行为表达**：记忆注入 LLM 上下文，驱动**说什么、表现什么情绪、何时主动搭话、主动频率**。用户随口表达的偏好（"我喜欢你多笑"）写入记忆后，LLM 在后续交互中更频繁地产出 `happy` affect——伙伴"笑得更多"。

**核心不变量**：记忆驱动的个性化通过**运行时行为**（affect / 言语 / 主动频率）实现，**不通过 clip 重生实现**。clip 只随角色定义变更而重生。这避免了"每学到一条新偏好就重生成全部视频"的不可行开销，同时让伙伴随关系深入而"表现得不一样"。

**互动统计信号**：高频戳/拖/对话活动通过 `companion.record_interaction_stats`（无 LLM）按 UTC 自然日聚合到 `Memory(context="interaction_stats:<date>")`，为后续 LLM 提供「用户当日活跃度 + 高峰时段」信号，区别于单条 user_profile / 个人记忆的细粒度反馈。门限为 poke、drag、chat_turn 三者各自 ≥ 10（双门限），避免日常轻度使用产生噪音。

---

## 7. 安全与准入控制 (Security & Trust)

### 7.1 物理与凭证隔离
- **大脑**（云端）不接触用户本地操作系统；即便 prompt 注入攻陷伙伴人格，也在受限用户账户下执行本地工具。
- **手脚**（Runner）零 Token 运行，无法越权请求 Backend 管理级 API。
- **枢纽**（Desktop）通过 Electron 主进程 `safeStorage` 将 JWT 加密落地在 `agent-session.json`。Renderer 与 Preload 无法接触 safeStorage 接口，阻断 XSS 窃取凭证。
- **用户隐私数据**：角色定义与形象资产归属于该用户，按用户维度隔离，不跨用户共享、不下发给他租户。

### 7.2 安全防护网 (Defense Layers)

#### 1. 输入清洗与指令包围 (Untrusted Wrap)
- Backend 从外部（Web 搜索、浏览器抓取、MCP 外部资源）获取的所有不可信文本，注入 LLM 上下文时强制用 `<untrusted_tool_result>` 标签包裹。
- 注入防范：系统提示词硬编码 `[OUT-OF-BAND USER MESSAGE]`（STEER_CHANNEL_NOTE）对抗恶意提示词篡改。
- 工具预留键防注入：禁止大模型在工具入参里覆盖 `user_id` / `llm_config` / `user_settings`。
- **角色定义防篡改**：角色定义作为系统提示词的一部分，同样受 reserved 键与不可信包围机制保护，防止用户对话内容注入改写伙伴人格。

#### 2. 本地执行拦截 (Soft & Hard Blocks)
- **硬阻断**（`utils/file_safety.py`）：严禁修改或读取系统级及凭证文件（`.ssh/`、AWS/GCP credentials、`/etc/passwd` 等）。Windows 上比对前缀时统一转斜杠 `/` 并强制小写，自动扫描锁定动态挂载盘符。
- **软防线**（`file_tools.py`）：三类跨域写操作抛出 LLM 可见警告，仅当用户显式许可并在入参注入 `cross_profile=True` 才放行：
  1. `cross-profile`：写入另一个用户的 Skills/Memories 配置。
  2. `sandbox-mirror`：写入主机侧绑定的容器任务镜像路径。
  3. `container-mirror`：写入容器内部被重定向的 `/root/` 敏感路径。

#### 3. 动态扫描与防护 (Tirith & SSRF)
- **Tirith 扫描**：Runner 执行任何 shell 命令前拉起本地 `tirith` 模块对参数签名与静态审查。
- **SSRF 拦截**：`browser_*` 导航或 `send_message` Webhook 触发前，`getaddrinfo` 解析目标 IP，强制阻断对 loopback、RFC 1918 私有子网、CGNAT、云厂商 Metadata（169.254.169.254）的连接。

---

## 8. 错误契约与韧性设计 (Error Contract)

### 8.1 错误分层模型

```
┌────────────────────────────────────────────────────────────────┐
│   REST API 异常 / WebSocket 联通断开                             │
│   - REST 返回 {error, reason, status} 结构                      │
│   - WebSocket 关闭码感知：1008 (鉴权失效) 立即退出重连流程        │
└──────────────────────────────┬─────────────────────────────────┘
                               │
┌──────────────────────────────▼─────────────────────────────────┐
│   JSON-RPC 协议异常                                              │
│   - 标准 JSON-RPC 2.0 错误码 (-32700 到 -32603)                  │
│   - -32603 (内部错误) 抛至客户端前脱敏，不暴露栈底敏感路径        │
└──────────────────────────────┬─────────────────────────────────┘
                               │
┌──────────────────────────────▼─────────────────────────────────┐
│   Runner 本地工具执行异常                                        │
│   - 返回 {ok: false, result: None, error: "<redacted_error>"}   │
│   - 全局 regex 脱敏，过滤 sk- / ghp_ / 私钥等凭证内容            │
└────────────────────────────────────────────────────────────────┘
```

### 8.2 错误分类管道 (Failover Reason)
云端 `error_classifier.py` 以 8 步优先级过滤（Provider 规则 ↔ 状态码 ↔ 错误代码 ↔ 异常特征 ↔ SSL 传输 ↔ 断连 ↔ 启发式），将所有 API 层或依赖项错误收拢为 21 种 `FailoverReason`：
- **计费与限流** (`billing`, `rate_limit`)：触发客户端自动退避重试或切换凭证。
- **附件获取异常** (`attachment_fetch_failed`)：LLM 获取多模态图片/视频附件失败时，拦截 Proxy 端原始 SDK 报错，向用户返回独立、明确的 Proxy 接入报错，避免误导性触发 LLM 回退逻辑。
- **形象生成失败**：作为陪伴场景的关键路径，形象生成失败需向用户返回可理解的友好提示并支持重试，不暴露生图服务原始错误（详见 backend/README.md）。

---

## 9. 自更新生命周期 (Self-Update)

Desktop 的更新通过 `/api/update` 获取 Electron 二进制与 Runner wheel 包，本地原子替换。为确保升级中途断网或崩溃不会"变砖"，采用**两阶段自更新契约**：

```
[OLD Electron 运行中]
  │
  ├─ 1. 定时后台自检最新版本 (30s)
  ├─ 2. 下载新版 Electron 二进制，触发 Runner Staging Prefetch
  ├─ 3. 新版 Runner wheel 和 server.py 下载至 $DESKAGENT_HOME/runner.staging/
  └─ 4. 强校验公钥签名 (update.pub) 与 SHA-512，写入升级 Sentinel
  │
[用户点击重启 (Restart & Install)]
  │
[NEW Electron 引导 Ready]
  │
  ├─ 5. 读取 Sentinel，调用 runnerBridge.stop() 释放旧 Python 句柄
  ├─ 6. pip install --upgrade "<wheel>" 在现有 venv 原地覆盖升级
  ├─ 7. 覆盖 server.py 并进行 Python 导入冒烟测试
  ├─ 8. 成功 → 启动 Runner 正常工作
  └─ 失败 → 回滚分支：清理 Sentinel，旧版 Runner 降级启动并向用户警告
```
- **核心约束**：Runner 的 venv 目录永不作重命名或移动，确保任意升级阶段崩溃时旧版 Runner 依赖树仍完全可用。
- 伙伴形象资产与角色定义云端持久化，自更新只影响本地代码与运行时，不触碰用户的伙伴身份。

---

## 10. 全局开发不变量 (Invariants)

**架构层（不可破坏）：**

1. **Runner 零凭证**：Runner 进程不能被授信任何 Backend Token，所有出站/LLM 请求必须向上借道 Desktop IPC 中转代理。
2. **错误遮蔽**：抛出到前端的 -32603 错误信息必须脱敏清洗，严禁包含数据库账号、服务器本地路径等栈帧细节。
3. **两阶段更新强校验**：禁止绕过 Stage 1 的 prefetch 逻辑直接覆盖本地 Runner 文件；公钥签名不匹配的升级包在 Staging 阶段直接拦截。
4. **ID 职责分立**：通信协议边界必须完成整型 `conversation_id` 与字符串 `session_id` 的显式转换；`call_id` 作为唯一 Future Key 标识生命周期。
5. **平台兼容性防御**：Windows 下进程控制或路径读取必须采用 `utils/pid.py` 内的 `psutil`/`taskkill` 原生封装，防止 Windows PTY 进程链悬挂。

**产品层（伙伴体验的底线）：**

6. **角色定义是伙伴行为的唯一真相源**：伙伴的所有输出风格、主动行为受持久化的角色定义约束；角色定义只能由用户显式发起变更，禁止 LLM 自行改写。
7. **形象资产归属用户**：生成的专属形象归属该用户，按用户维度隔离，不跨用户共享；形象在多次会话间保持一致，除非用户触发受控再生成。
8. **陪伴优先于工具**：产品决策发生冲突时，陪伴体验优先于工具效率。工具调用以"伙伴在帮忙"的叙事呈现，原始协议帧不直接暴露给最终用户（开发者视图除外）。
9. **伙伴表达永不空白**：任何动画状态或情绪 cue 无对应就绪 clip 时，Desktop 必须回退 idle loop + 状态轻量提示，用户永远不可见"该动画尚未生成"的空白或加载态。这是陪伴体验连续性的底线，也是 §6.3 语义/渲染解耦能成立的代价兜底。

---

## 11. 子模块详细 README.md 开发索引

模块级行为契约、文件树（带职责）、跨文件设计权衡分别记录在各子目录 README.md 中：

- **Backend（云端大脑）**：角色定义与形象资产的数据模型、生图 prompt 装配、记忆管理、LLM 编排——[backend/README.md](backend/README.md)
- **Runner（本地手脚）**：执行器与工具库、6 个终端环境后端、浏览器多后端——[runner/README.md](runner/README.md)
- **Desktop（伙伴载体 + 本地枢纽）**：桌面精灵形象渲染、onboarding/孵化流程、IPC 命名空间、自更新——[desktop/README.md](desktop/README.md)
- **Installer（安装器）**：引导协议、Python 运行时分发、首装进入"蛋"阶段——[installer/README.md](installer/README.md)
- **Scripts（发布与集成）**：构建链与导入规范检查——[scripts/README.md](scripts/README.md)
