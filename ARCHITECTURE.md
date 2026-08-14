# DeskAgent 架构设计

> **面向人类与 Agent 的系统总览。读代码前先读本文。**

DeskAgent 是一个**根据用户描述定制的、具有专属形象的陪伴型桌面伙伴**。用户首次安装后经一段 onboarding 对话定制伙伴的名字、性格、说话风格与外貌，系统据此生成专属桌面形象；此后伙伴常驻桌面、主动陪伴，并调用本机能力帮用户做事。

三个关键词决定一切产品与技术取舍：**定制**（形象与人格由用户定义并生成）、**陪伴**（主动、持续、有记忆的关系，而非一次性问答）、**伙伴**（交互对象是"他/她/它"，工具能力只是伙伴"会做的事"，不是产品的主角）。

**文档分层**:
- 产品设计意图（形象、动画、生命周期、onboarding、交互范式）见 [DESIGN.md](DESIGN.md)。
- 跨模块协议契约（JSON-RPC 方法 / 枚举 / 事件 / 安全 / 凭据）见 [PROTOCOL.md](PROTOCOL.md)。
- 本文聚焦**系统物理架构**——模块边界、为什么必须这样分离、通信链路的形态与权衡、跨模块不变量。
- 模块实现细节、文件树、配置项见各模块 `README.md`（[backend/](backend/README.md) / [client/](client/README.md) / [runner/](runner/README.md) / [installer/](installer/README.md)）。

---

## 1. 三模块物理解耦

系统在物理上拆分为三个核心子模块，各自承载不同职责、运行在不同环境、由不同信任域管辖：

| 层 | 所在 | 承载 | 为什么必须在物理上分离 |
|----|------|------|------------------------|
| **大脑** | Backend（云端） | LLM 编排、伙伴人格（角色定义 + 长期记忆）、形象资产生成、云端工具 | 伙伴的"性格与记忆"是跨设备、需多用户隔离、且依赖大模型与生图算力的核心资产，必须云端托管 |
| **手脚** | Runner（本地） | 终端、文件、浏览器、代码沙箱等本机操作能力 | 伙伴"帮用户做事"必须真正触碰用户本机；但本机操作不可上云，且执行环境必须与凭证隔离 |
| **形象与枢纽** | Client（本地） | 桌面精灵形象渲染、陪伴式交互、凭证持有、WS 中转、Runner 生命周期 | 伙伴要在用户桌面常驻可见、能听能说；同时是唯一可信的凭证持有点与本地/云端流量中转点 |

**核心不变量**：云端永不接触用户本机与凭证，本地 Runner 零凭证运行，Client 是唯一的可信枢纽。即便伙伴人格被 prompt 注入攻陷，最坏情况也只是受限用户账户下的本地工具被调用，不会泄露云端凭证或影响其他用户。

伙伴的"人格"由云端持久化的**角色定义**驱动（注入每次对话的系统提示词）；伙伴的"形象"由云端生成的**形象资产**驱动（由 Client 拉取并渲染）。两者共同构成"这一个伙伴"的数字身份，归属于该用户。

---

## 2. 架构拓扑

```
┌─────────────────────────────────────────────────────────────┐
│                        Backend                              │
│                   (云端大脑 / FastAPI)                        │
│  - 伙伴人格：角色定义持久化、长期/短期记忆管理                │
│  - 专属形象资产生成（portrait + 3D 模型 + 纹理 + 换装）       │
│  - LLM 编排与系统提示词装配（角色定义注入每次对话）           │
│  - 云端工具执行（Web 搜索、TTS、主动消息 send_message…）      │
│  - 事件发布 / Cron 调度中转（Outbox，主动陪伴的基石）        │
└─────────────────────────┬───────────────────────────────────┘
                          │ WebSocket 长连接（带用户 JWT 鉴权）
                          │ /api/chat/ws?token=<jwt>
┌─────────────────────────▼───────────────────────────────────┐
│                       Client                               │
│              (桌面伙伴形象载体 + 本地枢纽 / Electron)          │
│  - 桌面精灵 3D 实时渲染（Three.js + 透明置顶窗口）与陪伴式交互 │
│  - onboarding：角色定义引导、portrait + 3D 模型拉取与孵化过渡 │
│  - 登录鉴权与用户凭证加密落盘（safeStorage，跨平台统一）      │
│  - 本地 WebSocket 服务端与 Runner 进程生命周期管理            │
│  - 双向工具调用路由及反向 RPC (Reverse RPC) 代理中转          │
│  - 统一自更新（Electron 二进制 + Runner wheel）               │
└─────────────────────────┬───────────────────────────────────┘
                          │ 本地 WebSocket（动态高位端口）
                          │ ws://127.0.0.1:<port>/rpc
┌─────────────────────────▼───────────────────────────────────┐
│                        Runner                               │
│            (本地手脚 / uv build wheel)                       │
│  - 本地环境执行器（PTY 终端、文件读写、代码沙箱）            │
│  - 动态 MCP 协议客户端（配置经 WS 协议从 Desktop 推送）       │
│  - 浏览器多后端控制（Playwright / Camofox）                  │
│  - 自动化安全审查拦截器（Tirith 扫描 / 写白名单防护）         │
└─────────────────────────────────────────────────────────────┘
```

并行的 **Installer（安装器 / Tauri 2）** 在首次安装时于本地引导 uv Python 3.13 运行时，解压释放 Runner wheel、默认配置与基础 Skills 到本地平台标准路径下；首装完成即进入"蛋"阶段（[DESIGN.md §5](DESIGN.md)）。

> **Client 的定位是双层叠加**：底层是可信枢纽（凭证、中转、Runner 编排、自更新——这部分是 backend/runner 复用所依赖的不变契约），上层是桌面伙伴形象与陪伴交互。两层共享同一个 Electron 主进程，但职责严格分离：枢纽层处理协议与安全，伙伴层处理形象渲染与用户体验。

---

## 3. 核心模块与职责矩阵

| 维度 | Backend（云端大脑） | Client（伙伴载体 + 本地枢纽） | Runner（本地手脚） |
|---|---|---|---|
| **运行环境** | 容器/云端服务器（Linux Docker） | 用户本机原生（Win/macOS） | 本地静默进程（venv Python 运行时） |
| **状态持有** | 伙伴角色定义、形象资产、长期记忆、PostgreSQL、LLM API 凭证、用户会话历史 | 用户身份 JWT（加密）、Runner 进程 PID、本地工具集 Schema 缓存、伙伴形象资产本地缓存 | 终端环境快照、CDP 浏览器会话、本地 MCP Server 句柄 |
| **核心职责** | 接收用户消息、装配角色定义 + 记忆上下文、流式调度 LLM、按需生成/再生形象资产、解析工具调用并路由 | 3D 实时渲染桌面伙伴形象、承载陪伴式交互、引导 onboarding；维护本地安全防线、中转 WS 工具帧、代理 Runner 的反向 LLM 请求 | 纯粹执行底层工具逻辑，上报真实可用的工具 Schema 列表 |
| **安全准则** | `<untrusted_tool_result>` 包裹一切外部输入；Reserved 键覆盖保护；脱敏日志；角色定义属用户隐私数据 | Renderer 进程隐藏 JWT；仅暴露本地文件系统代理拦截 | Hardline 危险命令阻断；Windows 路径不敏感写限制；SSRF 防护 |

> 模块实现细节（依赖方向、文件树、provider 协议对比、错误分类管道等）见各模块 `README.md`。

---

## 4. 通信与消息协议

全链路采用 **JSON-RPC 2.0** 封装双向流量。这一协议层是 backend/runner 复用的核心契约。所有方法枚举、事件类型、错误信封、asset 签名见 [PROTOCOL.md](PROTOCOL.md)。

### 4.1 协议链路映射

#### A. Backend ↔ Client

单一 WebSocket 通道 `/api/chat/ws?token=<jwt>`，承载所有流式对话、控制事件、工具触发，以及形象资产下发与 onboarding 控制信令。**普通 chat / tool 类方法**见 [backend/README.md](backend/README.md)；**伙伴生命周期专用的扩展方法**（onboarding / avatar / companion / model / tts）见 [PROTOCOL.md §1.1](PROTOCOL.md)。

**伙伴层协议扩展**：在标准 JSON-RPC 信封基础上新增伙伴生命周期的控制信令，Client 侧的消费状态机见 [DESIGN.md §2](DESIGN.md)（动画状态机）+ §5（onboarding）+ §6（持续陪伴）。

#### B. Client ↔ Runner

本地环回 WebSocket `ws://127.0.0.1:<port>/rpc`。Client 充当 RPC Server，Runner 启动时作为 Client 主动连入。**选用 WebSocket 而非 stdio 重定向的权衡**：
1. 避免 C 库底层日志或 Python `print` 污染标准输入输出帧。
2. 全双工并发，按 `id` 异步匹配响应，避免进程读写阻塞与全局锁。

完整方法清单（`runner_ready` / `get_tools` / `execute_tool` / `deskagent.info` / `mcp.reload` / `request_llm`）与 `capabilities` payload 定义见 [PROTOCOL.md §2](PROTOCOL.md) 与 [runner/README.md](runner/README.md)。

### 4.2 核心数据流

#### I. 标准工具调用流（伙伴"帮忙"的底层通道）

```
LLM (Generate tool_call)
    │
    ▼ (Stream output)
Backend [orchestrator]
    │  1. 拦截 reserved 字段、检查 iteration_budget
    │  2. 创建 ipc.create_future(user_id, call_id)
    ▼ (WebSocket Push: method="event", type="tool.call")
Client [runner-bridge]
    │  3. 路由分发，由 IPC Bridge 转换帧
    ▼ (Local WS: method="execute_tool", params={"name", "args"})
Runner [server.py]
    │  4. Tirith / SSRF 扫描与文件写保护校验
    │  5. 本地执行（PTY / Playwright 等），清理控制字符与脱敏
    ▼ (Local WS Response: result={"stdout", "exit_code"})
Client [runner-rpc-ws]
    │  6. 捕获连接异常，Runner 离线快速 fail-fast
    ▼ (WebSocket Request: method="tool.result", params={"call_id", "result"})
Backend [api/v1/chat]
    │  7. 匹配 ipc.resolve_future(user_id, call_id) 唤醒 chat_service 协程
    ▼
LLM (Receive tool result & continue)
```

> **产品不变量**：工具调用在用户侧以"伙伴在帮忙"的叙事呈现，原始 `tool.call` / `tool.result` 帧不直接暴露给最终用户（开发者视图除外）。

#### II. 反向 RPC 流（Runner 借大脑）

本地 Runner 工具（如 `vision_analyze` 或动态 MCP）需要借助大模型能力，但**不持有任何云端凭证/Token**，通过 Client 代理转发：

```
Runner ──(Local WS: method="request_llm")──> Client ──(HTTP POST: /api/llm/completion)──> Backend ──> LLM
                                                │
                                        (Client JWT 鉴权)
```

**速率守卫**：Client 转发前统计单会话请求次数与载荷大小（硬上限 200 帧 / 1MB），防止 Runner 工具逻辑失控刷爆 LLM 额度。

#### III. 本地文件系统代理拦截

受限于沙箱或 Docker 容器无法看到的本地环境信息，Client 在 Renderer 发出 WS 请求前先行拦截处理（具体方法与实现见 [client/README.md](client/README.md)）：
- `config.get({key: "project"})`：Client 本地直接读取 `.git/HEAD` 恢复分支状态，不上报云端。
- `complete.path({word, cwd})`：`@` 路径补全时拦截，Client 主进程调用本地文件目录列表。

#### IV. 伙伴表达事件流（情绪 + 动画资产）

伙伴"如何表达"由 Backend 产出语义、Client 负责渲染，两者经 WS 解耦。情绪 cue 与动画资产是两条独立的流，互不阻塞：

**affect 流（情绪随话语同行）**：

```
对话响应 / send_message 主动消息
    │  响应帧内联 affect: {emotion} 字段
    ▼
Client 状态机：EMOTIONAL(affect) → SPEAKING(TTS) → IDLE
    │  affect 缺失时回退 SPEAKING(generic) → IDLE
```

**3D 模型流（Tripo3D 生成 + 动画注入）**：

```
Backend POST /api/companion/model（种子图 → Tripo3D image-to-model → rig → 注入 morph）──event: model.ready──> 客户端加载 GLB + 注入 TS 动画 clip + 状态机绑定
    │                                          │
    │  (客户端调 GET /api/companion/model 查状态)  └─ 生成中 / 生成失败 / GLB 加载失败 → 静态精灵相册（POST /api/companion/sprite：LLM 语义匹配已有相册，未命中则以种子图为 subject_reference 生成、服务端纯白→alpha）→ 相册不可用时程序化蛋形兜底（仅引导前期 / 绝对兜底）
```

**Blender+LLM 回退流（provider="blender_llm" 或 Tripo3D 不可用时）**：

```
Backend POST /api/companion/model {provider: "blender_llm"}
  →  LLM vision 分析三视图 → 生成 bpy 代码（_build_body 函数体）
  →  Blender --background 执行合并脚本 → GLB + Cycles CPU 预览
  →  LLM 对比预览 vs 种子图 → 精修 / 修复
  →  GLB 骨骼名验证通过 → 复用 inject_morph_targets.py 注入 44 ARKit blendshapes
  →  保存 + 激活 + emit model.ready (provider="blender_llm")
```

迭代上限 `BLENDER_LLM_MAX_ITERATIONS`（默认 10）× 单次 Blender 调用超时 `BLENDER_LLM_TIMEOUT` 秒（默认 600）—— 最坏情况 ~100 分钟一次生成；适合夜间离线场景，不阻塞交互 UI。model.gen.progress 事件携带 `provider` 字段供客户端做 UX 提示。

**关键解耦原则**:
- **inline affect**：情绪 cue 随其所属话语在同一响应帧（`message.complete`）下发，Client 无需二次猜测"这句话该配什么情绪"。独立的 `companion.affect` 事件用于非言语的情境化情绪反应。
- **语义/渲染解耦**：Backend 只产出 emotion + 可选 locale 语义，绝不指定渲染方式或像素坐标。客户端据 3D 模型可用动画与 morph target 决定渲染。这条解耦使模型加载不影响语义层（详见 §6.3）。

---

## 5. 事件与 Cron 调度（主动陪伴的基石）

**陪伴区别于工具的关键，在于伙伴会主动找用户。** 系统以 PostgreSQL LISTEN/NOTIFY + `ws_events` outbox 表支撑毫秒级、可水平扩展的事件分发，使伙伴的主动问候、定时提醒、情境化闲聊无需轮询即可送达：

1. **写出事件**：业务或 Cron 协程将待发送的 WS 帧写入 `ws_events` 表。
2. **数据库触发器**：PostgreSQL STATEMENT 级触发器在 INSERT 时自动发出 `NOTIFY ws_events_channel`。
3. **副本认领（Atomic Claim）**：每个 Backend 副本独立 `LISTEN`，收到唤醒后执行 `DELETE ... RETURNING`；行锁保证仅一台副本原子获取并消费该行。调度器 tick 只写入事件不 await WS 发射，避免慢客户端拖垮事务。

`send_message`（主动消息）、Cron（定时任务）、形象/角色变更通知等所有"伙伴主动行为"都经此通道下发至 Client。实现细节见 [backend/README.md §Cron 与事件下发](backend/README.md)。

### 5.1 打扰档位约束

所有"伙伴主动行为"受三档打扰等级约束（积极主动 / 常规 / 保持安静），档位由用户设置 + Client 检测到的用户活动共同决定，Client 经 `companion.set_disturbance_tier` 上报。

档位状态分两层（实现细节见 [client/README.md](client/README.md)）：
- `$userPreferredTier`：用户手动选择，源真值，持久化在 `localStorage`。
- `$effectiveTierOverride`：活动感知器写入的临时覆盖，null 表示无覆盖。
- `$effectiveTier = $effectiveTierOverride ?? $userPreferredTier`：其余模块读取这一原子做静默判定。

**Client 是档位的唯一权威**：它持有用户偏好 + 活动上下文，独立计算 effective 值（应用「手动 quiet 永远不被覆盖」+ immersive/fullscreen → quiet 规则），并通过 `companion.set_disturbance_tier` 把最终结果单向推给 Backend。Backend 只镜像这个值供 server-side gate（`send_message_tool`、`cron._kick_autonomous_turn`）使用——既不独立推导，也不存储 user_preferred 或 focus_context。

**消息与情绪是两个独立通道**：保持安静 / 屏幕锁定时 Backend 静默切断主动消息推送（`send_message_tool` 在 `quiet` 档把消息文本吞掉），但 LLM 推理出的 affect 经独立的 `companion.affect` 事件流出——即**断消息不断 affect**，Client 收到后切 EMOTIONAL 状态但不弹气泡、不做 TTS。

### 5.2 情境化 affect

用户长时间无活动时，Client 的 idle 轮询跨过阈值时调 `companion.check_affect {idle_seconds, local_hour}`，Backend 加载 persona + 最近记忆跑一次 LLM 推理，决定是否 emit `companion.affect`。**触发时机由 Client 控制（知道真实 idle 状态），情绪推理由 Backend LLM 承担（有 persona + 记忆）**——各取所长，Client 不退化成规则驱动的文案池。

### 5.3 单实例语义

`disturbance_tier` 与 IPC future 由 process-local 状态承载，**架构不支持多实例水平扩展**。任何"水平扩容"需求必须先把这两处迁出进程并相应修改多个 IPC 路径——本项目不准备这条路径，按单实例设计并部署即可。

---

## 6. 角色定义与表达层契约

Backend ↔ Client 之间的核心契约：角色定义如何驱动伙伴、形象资产如何生成与下发、情绪如何表达。**所有枚举（emotion / locale）、事件类型（`companion.affect` / `model.ready` / `wardrobe.updated` 等）、asset 签名 TTL、错误信封**见 [PROTOCOL.md §1](PROTOCOL.md)。

### 6.1 角色定义（唯一真相源）

onboarding 产出的结构化角色定义持久化在 Backend 用户维度，作为系统提示词的一部分注入该用户的每次对话，驱动伙伴的说话风格、性格表现与主动行为倾向。角色定义是伙伴行为的**唯一真相源**：变更只能由用户显式发起（重新进入角色编辑），禁止 LLM 自行改写。

### 6.2 形象资产（跨模块契约）

伙伴的视觉表达由 portrait、3D 模型、换装三层资产构成，均归属用户、在用户维度持久化。资产体系的产品形态、用途、换装设计见 [DESIGN.md §1](DESIGN.md)；此处只锁定跨模块契约：

- **资产归属用户维度**，不跨用户共享、不下发到他租户。
- **portrait 拆为 avatar + seed 配对**：avatar 是聚焦头部细节的半身像（onboarding 身份确认、设置页展示、聊天头像），seed 是三视角全身参考图（3D 纹理生成的输入）。两张图任一失败即整体失败；avatar prompt 硬性包含「纯白平面背景，无场景、无渐变、无阴影」子句（chroma-key 渲染依赖）。seed prompt 聚焦**身体轮廓锚点**（体型、五官、发型发色、标志性细节），参照装限定为**简单、不遮蔽人物轮廓特征**的款式，方便 Tripo3D 准确建模与绑骨——三视图之外的服饰由后续换装系统独立表达。
- **prompt 增强是读取型操作**——角色定义不被 LLM 改写，LLM 异常向上传播。
- **3D 模型由 Tripo3D / Blender 生成并启用 Draco 压缩**：以 seed 种子图为输入生成 rigged GLB，注入 44 ARKit morph targets，导出阶段完整启用 Draco 几何压缩（体积缩减 5–10×）；动画由客户端 TypeScript 关键帧注入。失败时客户端渲染程序化蛋形兜底角色。
- **Blender+LLM 回退管线**（当 Tripo3D 不可用时）：LLM 分析三视图→自由形式 bpy 代码→Blender headless 执行→预览渲染对比→迭代精修（默认 10 轮）。Client `provider="blender_llm"` 显式选择启用，或 Tripo key 缺失 / 积分为 0 / 显式余额耗尽时自动启用。模型质量显著低于 Tripo3D（无 PBR 纹理、自由形式几何），仅作 last-resort 兜底；触发条件在 `model.gen.progress` 事件的 `provider` 字段中暴露给客户端。
- **portrait 重生不触发模型失效**：模型只随物种变更或用户显式请求重生。换外观 = 换装（`POST /api/companion/wardrobe/preview` 单入口，由一次 LLM 路由决定走贴图热替 `kind=texture`、几何服装 `kind=garment` 或挂件 `kind=accessory`，装配契约见 [PROTOCOL.md §1.6](PROTOCOL.md)），不重生模型。
- **资产传输与内容寻址本地缓存**（契约见 [PROTOCOL.md §1.4](PROTOCOL.md)）：服务端模型/资产接口支持 HTTP Range（206 断点续传与 416 校验）与 ETag（SHA-256）；Client 主进程按 `content_hash` 持久化到 `$DESKAGENT_HOME/cache/models/`，无网络开销秒级命中；渲染端经 `DRACOLoader` 流式边下边解。
- **受控再生成**：形象在多次会话间保持稳定。变更只在用户主动要求时发生（重生 portrait / 重生模型 / 换装）。
- **静态精灵相册是 3D 的同级后备而非装饰**：覆盖无 `tripo_api_key` / 余额耗尽 / 生成失败（单视图模式无 Blender 回退，静态精灵是唯一后备）与模型重生成、换模空挡期。相册按用户维度存储、按 `avatar_id` 失效（头像重生即整体作废重生成）；生成走常规 image-gen provider 链（不做特殊排序）；身份一致性由种子图 `subject_reference` + persona 外貌锚点保证，透明背景由服务端纯白→alpha 后处理产出（MiniMax 仅出 JPEG，无法直出透明）。

### 6.3 表达层契约

伙伴"说什么"由 LLM 产出，"怎么动、什么情绪"由 Client 渲染。**完整 emotion / locale 枚举、affect 解析路径、事件流**见 [PROTOCOL.md §1.3](PROTOCOL.md)。

核心契约：
- **情绪 cue（affect）**：Backend 在对话响应/主动消息中携带 `affect: {emotion}` 语义字段，Client 据此驱动动画状态机。emotion 枚举（17 项）由 Backend allowlist 与客户端 morph target 目录**双端对齐**，未覆盖的 emotion 一律按 `neutral` 处理。
- **空间 cue（spatial）**：Backend 可在 `message.complete` 的 `affect` 中附带可选 `locale`（`home / chat / perch / roam / sleep`）与可选 `target`（窗口/进程名关键字）。**Backend 不产出像素坐标**——Client 据 locale + 当前空间状态决定最终位置与 locomotion；`target` 仅在 `perch` 时由 Client 经 `system.get_windows` 解析为窗口几何后计算 perch 点。
- **语义与渲染解耦**：Backend 只产出 emotion + 可选 locale 语义，绝不指定渲染方式或具体坐标。客户端据 3D 模型可用动画与 morph target 决定渲染——emotion 经 MorphController 映射到模型注入的表情 morph，无对应 morph 时回退 idle 动画。这使模型加载与语义层互不阻塞。
- **affect 继承角色定义的抗注入保证**：affect 由已注入角色定义的 LLM 产出，自然符合人格；角色定义本身受 §7 reserved 键与不可信包围机制保护，affect 因此继承同一保证，无需额外的情绪过滤层。
- **affect 与 text 同帧，TTS 由 Client 拉取式合成**：对话响应的 `message.complete` 帧内联 `{text, affect}`，不内联 TTS 音频。Client 收到后先据 affect 切 EMOTIONAL，再据 text 拉 TTS 切 SPEAKING，保证情绪基调先于语音进入。Backend 只产出语义（emotion + text），TTS 合成归 Client 渲染层。
- **user_profile 自动召回**：5 条结构化用户字段（称呼/性别/年龄段/爱好/自由文本）在 chat 入口渲染成 markdown 片段注入 system prompt stable 段，紧跟角色定义之后。LLM 每个 turn 都能直接看到用户身份事实，不依赖 `memory_recall` 工具调用——§6.4 双层模型中"记忆驱动行为"在结构化字段上不靠 LLM 是否记得调工具来决定。

### 6.4 行为驱动的双层模型

伙伴"做什么、怎么做"由两个独立来源驱动，两者不可混淆、也不可错配：

- **角色定义（静态，用户定义）→ 视觉身份与资产生成**：portrait 的生图 prompt 从角色定义装配（§6.2）。换风格 = 改角色定义 → 重生 portrait + 再生纹理。3D 模型按用户显式请求重生，不随 portrait 重生。这是伙伴"长什么样、动作风格如何"的唯一来源。
- **记忆（动态，随互动累积）→ 运行时行为表达**：记忆注入 LLM 上下文，驱动**说什么、表现什么情绪、何时主动搭话、主动频率**。用户随口表达的偏好（"我喜欢你多笑"）写入记忆后，LLM 在后续交互中更频繁地产出 `happy` affect——伙伴"笑得更多"。

**核心不变量**：记忆驱动的个性化通过**运行时行为**（affect / 言语 / 主动频率）实现，**不通过模型重生实现**。模型只随物种变更或用户显式请求重生。这避免了"每学到一条新偏好就重生成全部模型"的不可行开销，同时让伙伴随关系深入而"表现得不一样"。

**互动统计信号**：高频戳/拖/对话活动通过 `companion.record_interaction_stats`（无 LLM）按 UTC 自然日聚合到 `Memory(context="interaction_stats:<date>")`，为后续 LLM 提供「用户当日活跃度 + 高峰时段」信号。门限为 poke、drag、chat_turn 任一 ≥ 10（OR 门限）即写入汇总，单类高频互动也进入夜间 LLM 视野。

---

## 7. 安全与准入控制

### 7.1 物理与凭证隔离
- **大脑**（云端）不接触用户本地操作系统；即便 prompt 注入攻陷伙伴人格，也在受限用户账户下执行本地工具。
- **手脚**（Runner）零 Token 运行，无法越权请求 Backend 管理级 API。
- **枢纽**（Client）通过 Electron 主进程 `safeStorage` 将激活码加密落地在 `agent-session.json`。Renderer 与 Preload 无法接触 safeStorage 接口，阻断 XSS 窃取凭证（跨平台：Windows DPAPI / macOS Keychain / Linux libsecret）。
- **用户隐私数据**：角色定义与形象资产归属于该用户，按用户维度隔离，不跨用户共享、不下发给他租户。

### 7.2 安全防护网（Defense Layers）

#### 1. 输入清洗与指令包围
- Backend 从外部（Web 搜索、浏览器抓取、MCP 外部资源）获取的所有不可信文本，注入 LLM 上下文时强制用 `<untrusted_tool_result>` 标签包裹。短字符串（默认 32 字符）不包——注入风险低 + 节省 token。
- 系统提示词硬编码 `[OUT-OF-BAND USER MESSAGE]` 对抗恶意提示词篡改。
- **Reserved Keys 防注入**（完整列表见 [PROTOCOL.md §5.1](PROTOCOL.md)）：禁止大模型在工具入参里覆盖保留键（`user_id` / `llm_config` / `user_settings`）。**角色定义防篡改**：角色定义作为系统提示词的一部分，同样受此保护。

#### 2. 本地执行拦截
- **硬阻断**：严禁修改或读取系统级及凭证文件（`.ssh/`、云厂商 credentials、`/etc/passwd` 等）。Windows 上比对前缀时统一转斜杠 `/` 并强制小写，自动扫描锁定动态挂载盘符。
- **软防线**：三类跨域写操作抛出 LLM 可见警告，仅当用户显式许可并在入参注入 `cross_profile=True` 才放行：
  1. `cross-profile`：写入另一个用户的 Skills/Memories 配置。
  2. `sandbox-mirror`：写入主机侧绑定的容器任务镜像路径。
  3. `container-mirror`：写入容器内部被重定向的 `/root/` 敏感路径。

#### 3. 动态扫描与防护
- **Tirith 扫描**：Runner 执行任何 shell 命令前拉起本地 `tirith` 模块对参数签名与静态审查。
- **SSRF 拦截**：浏览器导航或 `send_message` Webhook 触发前，`getaddrinfo` 解析目标 IP，强制阻断对 loopback、RFC 1918 私有子网、CGNAT、云厂商 Metadata（169.254.169.254）的连接。建连时再经 httpx `event_hooks.connect` 复核，阻断预检与建连之间的 DNS 重绑定。

#### 4. 凭据边界
- API key 永不离后端（fingerprinting）：`GET /api/user/model-config` 只返回 `sk-…XX` 形式的指纹 + `_set` 布尔，原始 key 不返回。详细见 [PROTOCOL.md §5.4](PROTOCOL.md)。
- 自更新签名：Electron 二进制走 `electron-updater` RSA；Python runner wheel 走 `scripts/secrets/update.pub` RSA + SHA-512 双重校验（两阶段契约见 [PROTOCOL.md §5.6](PROTOCOL.md)）。
- Skills 走单独通道：installer 首装 seed，client 自更新不下载（见 [installer/README.md](installer/README.md)）。

---

## 8. 错误契约与韧性设计

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

### 8.2 错误分类管道

云端把所有 API 层或依赖项错误收拢为 `FailoverReason` 集合（21 种），由 8 步优先级过滤（Provider 规则 ↔ 状态码 ↔ 错误代码 ↔ 异常特征 ↔ SSL 传输 ↔ 断连 ↔ 启发式）。分类决定恢复策略（退避重试 / 凭证轮换 / 压缩上下文 / 不重试）。完整列表见 [backend/README.md §错误分类管道](backend/README.md)。

**关键约束**:
- 流式 chat 一旦首个 chunk 已发出，任何 provider 失败**不再 fallback**——用户已经看到部分输出，切换 provider 只会造成 transcript 截断。
- 形象生成失败：作为陪伴场景的关键路径，需向用户返回可理解的友好提示并支持重试，不暴露生图服务原始错误。
- 附件获取异常：LLM 获取多模态图片/视频附件失败时，拦截 Proxy 端原始 SDK 报错，向用户返回独立、明确的 Proxy 接入报错，避免误导性触发 LLM 回退逻辑。

---

## 9. 自更新生命周期（架构意图）

Client 的更新通过 `/api/update` 获取 Electron 二进制与 Runner wheel 包，本地原子替换。**两阶段契约**避免升级中途断网或崩溃变砖：

- **Stage 1（Prefetch）**：定时后台自检 → 下载新版 Electron → 触发 Runner Staging Prefetch → 强校验公钥签名 + SHA-512 → 写升级 Sentinel。
- **Stage 2（Install,用户点 Restart & Install 后）**：释放旧 Python 句柄 → `pip install --upgrade` 原地覆盖 → 冒烟测试 → 成功启动新 Runner / 失败回滚到旧版。

完整签名校验、路径、错误处理细节见 [PROTOCOL.md §5.6](PROTOCOL.md) 与 [scripts/README.md](scripts/README.md)。伙伴形象资产与角色定义云端持久化，自更新只影响本地代码与运行时，不触碰用户的伙伴身份。

---

## 10. 全局开发不变量 (Invariants)

**架构层（不可破坏）：**

1. **Runner 零凭证**：Runner 进程不能被授信任何 Backend Token，所有出站/LLM 请求必须向上借道 Client IPC 中转代理。
2. **错误遮蔽**：抛出到前端的 -32603 错误信息必须脱敏清洗，严禁包含数据库账号、服务器本地路径等栈帧细节。
3. **两阶段更新强校验**：禁止绕过 Stage 1 的 prefetch 逻辑直接覆盖本地 Runner 文件；公钥签名不匹配的升级包在 Staging 阶段直接拦截。
4. **ID 职责分立**：通信协议边界必须完成整型 `conversation_id` 与字符串 `session_id` 的显式转换；`call_id` 作为唯一 Future Key 标识生命周期（详见 [PROTOCOL.md §6](PROTOCOL.md)）。
5. **平台兼容性防御**：Windows 下进程控制或路径读取必须采用 Windows 原生封装，防止 Windows PTY 进程链悬挂（具体封装见 [runner/README.md §已知限制](runner/README.md)）。
6. **单实例部署**：`disturbance_tier` 与 IPC future 由 process-local 状态承载，架构不支持多实例水平扩展。

**产品层（伙伴体验的底线）：**

7. **角色定义是伙伴行为的唯一真相源**：伙伴的所有输出风格、主动行为受持久化的角色定义约束；角色定义只能由用户显式发起变更，禁止 LLM 自行改写。
8. **形象资产归属用户**：生成的专属形象归属该用户，按用户维度隔离，不跨用户共享；形象在多次会话间保持一致，除非用户触发受控再生成。
9. **陪伴优先于工具**：产品决策发生冲突时，陪伴体验优先于工具效率。工具调用以"伙伴在帮忙"的叙事呈现，原始协议帧不直接暴露给最终用户（开发者视图除外）。
10. **伙伴表达永不空白**：任何动画状态或情绪 cue 无对应就绪资产时，客户端必须回退 idle 动画或降级渲染，用户永远不可见"该动画尚未加载"的空白或加载态。渲染兜底按「GLB → 静态精灵相册 → 程序化蛋形」层级降级——无 3D 模型期间用户看到的仍是自己的角色（静态立绘按状态/情绪切换），蛋形仅在形象种子图产生之前或相册完全不可用时出现。这是陪伴体验连续性的底线，也是 §6.3 语义/渲染解耦能成立的代价兜底。

**安全层（Blender+LLM 信任边界）**：

11. **LLM 生成的代码在 Backend 信任边界内执行**：Blender+LLM 回退管线把 LLM 输出的自由形式 bpy 代码注入到 `llm_bpy_scaffold.py` 的 `_build_body(ctx)` 函数体中，并通过 `blender --background` 子进程执行。Blender 子进程与 Backend 同用户运行，可触达 Backend 信任域内的 OS 操作（文件系统、环境变量、子进程派生）。这不是新引入的攻击面——Backend 早已持有 LLM API key 与 DB 凭证，LLM 调用本身已是同等威胁向量——但显式记录在此供安全审计参考。LLM 视角图（人像种子图）原本就经 `<untrusted_tool_result>` 包裹送入 LLM 上下文，这条管线不扩大数据泄露面。

---

## 11. 子模块详细 README.md 开发索引

模块级行为契约、文件树（带职责）、跨文件设计权衡分别记录在各子目录 README.md 中：

- **Backend（云端大脑）**：角色定义与形象资产的数据模型、生图 prompt 装配、记忆管理、LLM 编排——[backend/README.md](backend/README.md)
- **Runner（本地手脚）**：执行器与工具库、6 个终端环境后端、浏览器多后端——[runner/README.md](runner/README.md)
- **Client（伙伴载体 + 本地枢纽）**：3D 实时渲染引擎（Three.js WebGL）、骨骼动画 + morph target 驱动、换装热替、onboarding/孵化流程、IPC 命名空间、自更新——[client/README.md](client/README.md)
- **Installer（安装器）**：引导协议、Python 运行时分发、首装进入"蛋"阶段——[installer/README.md](installer/README.md)
- **Scripts（发布与集成）**：构建链与导入规范检查——[scripts/README.md](scripts/README.md)
