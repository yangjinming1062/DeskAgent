# DeskAgent 架构设计

> **面向人类与 Agent 的系统总览。读代码前先读本文。**

DeskAgent 是一个**根据用户描述定制的、具有专属形象的陪伴型桌面伙伴**。用户在首次安装时以一颗"蛋"的形态见到它，通过一段 onboarding 对话描述自己想要的伙伴（名字、性格、说话风格、外貌与视觉风格偏好），系统据此即时生成属于该用户的专属桌面形象；此后用户直接与这个形象交互，伙伴常驻桌面、能主动陪伴、也能调用本机能力帮用户做事。

三个关键词决定了一切产品与技术取舍：**定制**（形象与人格由用户定义并生成）、**陪伴**（主动、持续、有记忆的关系，而非一次性问答）、**伙伴**（交互对象是"他/她/它"，工具能力只是伙伴"会做的事"，不是产品的主角）。

---

## 1. 核心设计思想

系统沿用以"云端思考 + 本地执行"为基础的三模块物理解耦，但在新的产品定位下重新解释其合理性：

| 层 | 所在 | 承载 | 为什么必须在物理上分离 |
|----|------|------|------------------------|
| **大脑** | Backend（云端） | LLM 编排、伙伴人格（角色定义 + 长期记忆）、形象资产生成、云端工具 | 伙伴的"性格与记忆"是跨设备、需多用户隔离、且依赖大模型与生图算力的核心资产，必须云端托管 |
| **手脚** | Runner（本地） | 终端、文件、浏览器、代码沙箱等本机操作能力 | 伙伴"帮用户做事"必须真正触碰用户本机；但本机操作不可上云，且执行环境必须与凭证隔离 |
| **形象与枢纽** | Desktop（本地） | 桌面精灵形象渲染、陪伴式交互、凭证持有、WS 中转、Runner 生命周期 | 伙伴要在用户桌面常驻可见、能听能说；同时是唯一可信的凭证持有点与本地/云端流量中转点 |

解耦带来的不变收益在新定位下依然成立：**云端永不接触用户本机与凭证，本地 Runner 零凭证运行，Desktop 是唯一的可信枢纽。** 即便伙伴人格被 prompt 注入攻陷，最坏情况也只是受限用户账户下的本地工具被调用，不会泄露云端凭证或影响其他用户。

伙伴的"人格"由云端持久化的**角色定义**驱动（注入每次对话的系统提示词）；伙伴的"形象"由云端生成的**形象资产**驱动（由 Desktop 拉取并渲染）。两者共同构成"这一个伙伴"的数字身份，归属于该用户。

---

## 2. 伙伴生命周期（核心产品流）

这是 DeskAgent 区别于一切既有桌面宠物 / 桌面 Agent 的核心流程，也是产品体验的骨架：

```
[安装完成]
    │
    ▼
┌─ 蛋 (Egg) ─────────────────────────────────────────────┐
│  角色定义完成前，Desktop 以"蛋"作为占位形象常驻桌面。    │
│  蛋是产品意象，技术本质是"形象生成未完成时的默认形象"。   │
└──────────────────────────┬─────────────────────────────┘
                           │  用户点击/唤醒，进入 onboarding
                           ▼
┌─ 角色定义 (Persona Definition) ────────────────────────┐
│  引导用户描述想要的伙伴：名字、性格、说话风格、外貌与    │
│  视觉风格偏好等。交互范式类似 agent 初始化时的基本信息   │
│  确认（对话式、可回退、用户拥有最终确认权）。            │
│  产出：一份结构化的角色定义，持久化在用户维度。          │
└──────────────────────────┬─────────────────────────────┘
                           │  用户确认
                           ▼
┌─ 形象生成 (Avatar Generation) ─────────────────────────┐
│  Backend 据角色定义装配生图 prompt，调用云端图片生成工具 │
│  产出专属形象资产，与角色定义一同在用户维度持久化。      │
└──────────────────────────┬─────────────────────────────┘
                           │  资产下发至 Desktop
                           ▼
┌─ 孵化 (Hatch) ─────────────────────────────────────────┐
│  Desktop 将桌面上的"蛋"替换为生成的专属形象，配以仪式感 │
│  过渡动画。这一刻是产品核心的情感锚点。                  │
└──────────────────────────┬─────────────────────────────┘
                           ▼
┌─ 持续陪伴 (Ongoing Companionship) ─────────────────────┐
│  • 伙伴形象常驻桌面（透明置顶窗口），可被用户随时唤起对话 │
│  • 伙伴可主动发起交互：问候、提醒、闲聊（由 Cron / 事件驱动）│
│  • 伙伴有长期记忆，随互动积累对用户的了解                 │
│  • 伙伴可调用 Runner 帮用户操作本机——叙事上是"伙伴在帮忙"│
└────────────────────────────────────────────────────────┘
```

后续可在此基础上扩展"成长 / 进化"机制（伙伴随互动阶段性地演变形象或人格），具体设计待补。

---

## 3. 架构拓扑 (Topology)

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

## 4. 核心模块与职责矩阵 (Responsibilities)

| 维度 | Backend (云端大脑) | Desktop (伙伴载体 + 本地枢纽) | Runner (本地手脚) |
|---|---|---|---|
| **运行环境** | 容器/云端服务器 (Linux Docker) | 用户本机原生环境 (Win/Mac/Linux) | 本地静默进程 (venv Python 运行时) |
| **状态持有** | 伙伴角色定义、形象资产、长期记忆、PostgreSQL、LLM API 凭证、用户会话历史 | 用户身份 JWT (加密)、Runner 进程 PID、本地工具集 Schema 缓存、伙伴形象资产本地缓存 | 终端环境快照、CDP 浏览器会话、本地 MCP Server 句柄 |
| **核心职责** | 接收用户消息、装配角色定义+记忆上下文、流式调度 LLM、按需生成/再生形象资产、解析工具调用并路由 | 渲染桌面伙伴形象、承载陪伴式交互、引导 onboarding；维护本地安全防线、中转 WS 工具帧、代理 Runner 的反向 LLM 请求 | 纯粹执行底层工具逻辑，上报真实可用的工具 Schema 列表 |
| **安全准则** | `<untrusted_tool_result>` 包裹一切外部输入；Reserved 键覆盖保护；脱敏日志；角色定义属用户隐私数据 | Renderer 进程隐藏 JWT；仅暴露本地文件系统代理拦截；屏幕录制通道防护 | Hardline 危险命令阻断；Windows 路径不敏感写限制；SSRF 防护 |

---

## 5. 通信与消息协议 (Protocols & Flows)

全链路采用 **JSON-RPC 2.0** 封装双向流量。这一协议层是 backend/runner 复用的核心契约，不随产品定位变化。

### 5.1 协议链路映射

#### A. Backend ↔ Desktop
单一 WebSocket 通道 `/api/chat/ws?token=<jwt>`，承载所有流式对话、控制事件、工具触发，以及形象资产下发与 onboarding 控制信令。
- **请求信封 (Request)**: `{"jsonrpc": "2.0", "id": "<call_id>", "method": "<method>", "params": {...}}`
- **事件推送 (Notification)**: `{"jsonrpc": "2.0", "method": "event", "params": {"type": "<event_type>", "payload": {...}}`

**伙伴层协议扩展**（onboarding / 形象与动画资产 / 情绪表达）：在上述信封基础上新增以下方法与事件，承载伙伴生命周期的控制信令。Desktop 侧的消费状态机见 [COMPANION_DESIGN.md §2](COMPANION_DESIGN.md#2-动画状态机)。

| 方向 | 方法 / 事件 type | 用途 |
|------|------------------|------|
| Desktop → Backend | `onboarding.get_state` | 查询已采集字段与下一步（断点恢复） |
| Desktop → Backend | `onboarding.submit` `{field, value}` | 逐字段增量持久化 onboarding 答案 |
| Desktop → Backend | `avatar.regenerate` `{feedback?}` | 重生 portrait，所有衍生 clip 失效并重排队列 |
| Desktop → Backend | `avatar.list_clips` | 查询 clip 目录与生成状态（就绪/排队/失败） |
| Backend → Desktop | `event.type="affect"` `{emotion}` | 情绪 cue，驱动 EMOTIONAL 状态（详见 §5.2.IV / §7.5） |
| Desktop → Backend | `companion.set_disturbance_tier` `{tier}` | 上报当前打扰档位（积极主动/常规/保持安静），约束 Backend 主动消息 |

clip 的就绪/失败通知**不另造事件**，直接复用既有的 `video_gen.completed` / `video_gen.failed`（[backend/README.md 视频生成](backend/README.md#视频生成)）：companion 服务以 portrait 为种子经图生视频生成 clip，走同一条 `media/video_jobs` 流水线，payload 中携带 scene 标识供 Desktop 绑定到对应状态。Desktop 另调 `avatar.list_clips` 查询整批 clip 目录与各自生成状态。

#### B. Desktop ↔ Runner
本地环回 WebSocket `ws://127.0.0.1:<port>/rpc`。Desktop 充当 RPC Server，Runner 启动时作为 Client 主动连入。
- 选用 WebSocket 而非 stdio 重定向的权衡：
  1. 避免 C 库底层日志或 Python `print` 污染标准输入输出帧。
  2. 全双工并发，按 `id` 异步匹配响应，避免进程读写阻塞与全局锁。

### 5.2 核心数据流

#### I. 标准工具调用流（伙伴"帮忙"的底层通道）
当云端 LLM 决定调用本地工具（如用户要伙伴整理某个文件夹，触发 `terminal` 或 `write_file`）：

```
LLM (Generate tool_call)
    │
    ▼ (Stream output)
Backend [core/chat_service.py]
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
Backend [routers/chat.py]
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
Backend clip 生成队列（portrait 种子图 + 场景文本 → 图生视频）──event: video_gen.completed──> Desktop 本地缓存 + 状态机绑定
    │                                          │
    │  (Desktop 调 avatar.list_clips 查进度)    └─ clip 缺失时该状态回退 idle loop
    └── portrait 重生 → 所有衍生 clip 失效重排队列
```

- **inline affect 原则**：情绪 cue 随其所属话语在同一响应帧下发，Desktop 无需二次猜测"这句话该配什么情绪"。独立 `event: affect` 仅保留给非言语的情境化情绪反应（如用户久未上线后的兴奋招呼），属罕见场景。
- **语义/渲染解耦**：Backend 只产出 `emotion` 语义，绝不指定 clip 文件名或渲染方式；Desktop 据本地可用资产决定如何渲染。这条解耦使 clip 的渐进生成不影响语义层（详见 §7.5）。

---

## 6. 事件与 Cron 调度（主动陪伴的基石）

**陪伴区别于工具的关键，在于伙伴会主动找用户。** 系统以 PostgreSQL LISTEN/NOTIFY + Outbox 表支撑毫秒级、可水平扩展的事件分发，使伙伴的主动问候、定时提醒、情境化闲聊无需轮询即可送达：

1. **写出事件**：业务或 Cron 协程（`core/cron.py` 推进 `cron_jobs` 状态；或 LLM 经 `send_message` 工具主动触发）将待发送的 WS 帧写入 `ws_events` 表。
2. **数据库触发器**：PostgreSQL 在 `ws_events` 上的 STATEMENT 级触发器写入时自动发出 `NOTIFY ws_events_channel`。
3. **副本认领（Atomic Claim）**：
   - 每个 Backend 副本独立通过 `asyncpg` 监听 `LISTEN ws_events_channel`。
   - 收到唤醒后各副本执行 `DELETE ... RETURNING`；行锁保证仅一台副本原子获取并消费该行，分发给连接其上的用户 WS。
   - **防阻塞**：调度器 tick 只写入事件，不 await 实际 WebSocket 发射，避免慢客户端拖垮事务。

`send_message`（主动消息）、Cron（定时任务）、形象/角色变更通知等所有"伙伴主动行为"都经此通道下发至 Desktop，再由伙伴形象以符合其人格的方式表达。

**打扰档位约束**：所有"伙伴主动行为"受三档打扰等级约束（积极主动 / 常规 / 保持安静），档位由用户设置 + Desktop 检测到的用户活动共同决定，Desktop 经 `companion.set_disturbance_tier` 上报当前生效档位。Backend 据此放行或抑制主动消息：**保持安静档阻断主动消息但不断 affect cue**——精灵不发消息打扰，仍可经 affect 表达情绪（如粘人型被冷落后的委屈 affect）。这条约束成立的前提正是 §7.5 的 affect/message 解耦。档位的行为细节见 [COMPANION_DESIGN.md §4.2](COMPANION_DESIGN.md#42-主动陪伴与打扰档位backend-驱动)。

---

## 7. 伙伴形象与角色系统

这是新定位下 Backend/Desktop 之间的核心新增契约。实现细节（schema、存储表、渲染管线）留给 [backend/README.md](backend/README.md) 与 [desktop/README.md](desktop/README.md)，ARCHITECTURE.md 只锁定跨模块的设计意图与不变量。

### 7.1 角色定义 (Persona Definition)
- onboarding 产出的结构化角色定义，持久化在 Backend 的用户维度。
- 作为系统提示词的一部分注入该用户的每次对话，驱动伙伴的说话风格、性格表现与主动行为倾向。
- 角色定义是伙伴行为的**唯一真相源**：变更只能由用户显式发起（重新进入角色编辑），不允许 LLM 自行改写。

### 7.2 形象与动画资产 (Avatar & Animation Assets)
伙伴的视觉表达由三层资产构成，均归属用户、在用户维度持久化，Desktop 拉取后本地缓存并渲染：

| 资产 | 形态 | 用途 |
|------|------|------|
| **portrait** | PNG | 视觉身份基准；由 Backend 据角色定义装配生图 prompt，调用 `backend_tools/image_generation_tool.py` 产出 |
| **loop clip** | 3–5s 透明背景循环视频 | 常驻状态承载，每个 clip 绑定一个动画状态（[COMPANION_DESIGN.md §2](COMPANION_DESIGN.md#2-动画状态机)） |
| **transition clip** | 一次性透明背景视频 | 仪式感时刻（孵化、问候、告别） |

- **图生视频契约**：所有 clip 由 Backend 以当前 portrait 为种子图、结合场景/动作描述文本，经图生视频能力（MiniMax Hailuo，复用 `video_generate` 工具的 `first_frame_image` 参数与 `media/video_jobs` 流水线）产出。portrait 既是视觉身份基准、也是全部 clip 的生成种子——同一颗种子图从机制上保证跨 clip 的角色一致性，无需额外的风格锁。
- **渐进式生成**：portrait + idle clip 在 onboarding 同步生成（批次 0）；其余 clip 按优先级后台排队——speaking/thinking/working（批次 1）→ 生命周期 clip（批次 2）→ 情绪变体（批次 3），就绪后经既有 `video_gen.completed` 事件下发。分批策略与降级细节见 [COMPANION_DESIGN.md §1.3](COMPANION_DESIGN.md#13-渐进式生成与不变量)。
- **衍生失效**：因 clip 是以 portrait 为种子的图生视频产物，portrait 重生（`avatar.regenerate`）时所有 clip 必然失配，须全部失效并从新种子重新排队，绝不跨 portrait 版本复用。
- **资产 URL 有 TTL**：provider 下载 URL 有时效（MiniMax 9h），Backend 已在服务端下载落盘并对 Desktop 暴露自有 `/api/media/files/<id>` URL；Desktop 收到后仍须立即本地缓存，不依赖该 URL 永久有效。
- Desktop 以透明置顶窗口将形象以桌面精灵形态常驻呈现（具体渲染技术——WebM alpha / sprite / 序列帧——是实现决策，由 desktop 子模块决定）。

### 7.3 一致性与受控变更
- **跨会话一致**：生成的形象在多次对话中保持稳定，构成伙伴的视觉身份。
- **受控再生成**：形象变更只在以下情况发生——用户主动要求（换风格、重新生成）、或未来扩展的"成长/进化"机制；任意一种都走 Backend 主导的再生成流程，并同步更新 Desktop 本地缓存。

### 7.4 已有云端工具在新定位下的复用映射
旧 backend 工具集恰好覆盖陪伴场景的核心能力，这是"为什么复用 backend 是合理的"的关键依据：

| 工具 | 旧定位 | 新定位（陪伴场景） |
|------|--------|---------------------|
| `image_generation_tool` | 通用图片生成 | **生成专属桌面形象** |
| `tts_tool` | 通用 TTS | **伙伴的语音**（让伙伴"能说"） |
| `send_message_tool` | 通用主动消息 | **伙伴主动发起对话**（问候/提醒/闲聊） |
| `web_tools` | 通用 Web 搜索 | **伙伴帮用户查信息、聊时事** |
| `memory`（tools_runtime） | 通用记忆 | **伙伴对用户的长期记忆**（陪伴的关系感） |

### 7.5 伙伴表达层契约 (Companion Expression Contract)
伙伴"说什么"由 LLM 产出，"怎么动、什么情绪"由 Desktop 渲染。以下是两者之间的语义契约：

- **情绪 cue（affect）**：Backend 在对话响应/主动消息中携带 `affect: {emotion}` 语义字段，Desktop 据此驱动动画状态机。emotion 为有限枚举集（`happy / sad / surprised / excited / confused / concerned / shy / proud / grateful / playful / bored` + `neutral`），可扩展——但每次扩展须同步 Backend 的产出 allowlist 与 Desktop 的 clip 目录，否则未覆盖的 emotion 一律按 `neutral` 处理。
- **语义与渲染解耦**：Backend 只产出 emotion 语义，绝不指定 clip 文件或渲染方式。Desktop 据本地可用资产决定渲染——有对应 clip 则播放，否则回退 idle loop + 状态轻量提示。这使 clip 渐进生成与语义层互不阻塞。
- **affect 与角色定义一致**：affect 由已注入角色定义的 LLM 产出，自然符合人格；角色定义本身受 §8.2 防篡改机制保护，affect 因此继承同一抗注入保证，无需额外的情绪过滤层。
- **TTS 与 affect 同帧**：对话响应帧中，TTS 音频信息与 affect 必须在同一帧下发（§5.2.IV 的 inline affect 原则），使 Desktop 能在 SPEAKING 前先播 EMOTIONAL(affect)、用对应情绪基调进入说话，而非事后补播情绪。
- **onboarding 断点恢复**：onboarding 采集的字段在 Backend 用户维度**逐字段增量持久化**（每提交一个 `onboarding.submit` 即落盘）。Desktop 启动时调 `onboarding.get_state`，未完成则从最后未答问题恢复，崩溃/退出不丢进度。

### 7.6 行为驱动的双层模型
伙伴"做什么、怎么做"由两个独立来源驱动，两者不可混淆、也不可错配：

- **角色定义（静态，用户定义）→ 视觉身份与资产生成**：portrait 与所有 clip 的生图/生视频 prompt 从角色定义装配（§7.2）。换风格 = 改角色定义 → 资产重生。这是伙伴"长什么样、动作风格如何"的唯一来源。
- **记忆（动态，随互动累积）→ 运行时行为表达**：记忆注入 LLM 上下文，驱动**说什么、表现什么情绪、何时主动搭话、主动频率**。用户随口表达的偏好（"我喜欢你多笑"）写入记忆后，LLM 在后续交互中更频繁地产出 `happy` affect——伙伴"笑得更多"。

**核心不变量**：记忆驱动的个性化通过**运行时行为**（affect / 言语 / 主动频率）实现，**不通过 clip 重生实现**。clip 只随角色定义变更而重生。这避免了"每学到一条新偏好就重生成全部视频"的不可行开销，同时让伙伴随关系深入而"表现得不一样"。未来"成长/进化"机制（§2）才会引入记忆驱动的阶段性 clip 重生。

---

## 8. 安全与准入控制 (Security & Trust)

### 8.1 物理与凭证隔离
- **大脑**（云端）不接触用户本地操作系统；即便 prompt 注入攻陷伙伴人格，也在受限用户账户下执行本地工具。
- **手脚**（Runner）零 Token 运行，无法越权请求 Backend 管理级 API。
- **枢纽**（Desktop）通过 Electron 主进程 `safeStorage` 将 JWT 加密落地在 `agent-session.json`。Renderer 与 Preload 无法接触 safeStorage 接口，阻断 XSS 窃取凭证。
- **用户隐私数据**：角色定义与形象资产归属于该用户，按用户维度隔离，不跨用户共享、不下发给他租户。

### 8.2 安全防护网 (Defense Layers)

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

## 9. 错误契约与韧性设计 (Error Contract)

### 9.1 错误分层模型

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

### 9.2 错误分类管道 (Failover Reason)
云端 `error_classifier.py` 以 8 步优先级过滤（Provider 规则 ↔ 状态码 ↔ 错误代码 ↔ 异常特征 ↔ SSL 传输 ↔ 断连 ↔ 启发式），将所有 API 层或依赖项错误收拢为 21 种 `FailoverReason`：
- **计费与限流** (`billing`, `rate_limit`)：触发客户端自动退避重试或切换凭证。
- **附件获取异常** (`attachment_fetch_failed`)：LLM 获取多模态图片/视频附件失败时，拦截 Proxy 端原始 SDK 报错，向用户返回独立、明确的 Proxy 接入报错，避免误导性触发 LLM 回退逻辑。
- **形象生成失败**：作为陪伴场景的关键路径，形象生成失败需向用户返回可理解的友好提示并支持重试，不暴露生图服务原始错误（详见 backend/README.md）。

---

## 10. 自更新生命周期 (Self-Update)

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

## 11. 全局开发不变量 (Invariants)

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
9. **伙伴表达永不空白**：任何动画状态或情绪 cue 无对应就绪 clip 时，Desktop 必须回退 idle loop + 状态轻量提示，用户永远不可见"该动画尚未生成"的空白或加载态。这是陪伴体验连续性的底线，也是 §7.5 语义/渲染解耦能成立的代价兜底。

---

## 12. 子模块详细 README.md 开发索引

模块级行为契约、文件树（带职责）、跨文件设计权衡分别记录在各子目录 README.md 中：

- **Backend（云端大脑）**：角色定义与形象资产的数据模型、生图 prompt 装配、记忆管理、LLM 编排——[backend/README.md](backend/README.md)
- **Runner（本地手脚）**：执行器与工具库、6 个终端环境后端、浏览器多后端——[runner/README.md](runner/README.md)
- **Desktop（伙伴载体 + 本地枢纽）**：桌面精灵形象渲染、onboarding/孵化流程、IPC 命名空间、自更新——[desktop/README.md](desktop/README.md)
- **Installer（安装器）**：引导协议、Python 运行时分发、首装进入"蛋"阶段——[installer/README.md](installer/README.md)
- **Scripts（发布与集成）**：构建链与导入规范检查——[scripts/README.md](scripts/README.md)
