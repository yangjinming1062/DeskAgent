# Backend

云端大脑——FastAPI + PostgreSQL + JWT。承载 DeskAgent 伙伴的"人格"（角色定义 + 长期记忆）与"形象"（专属形象资产生成与下发），负责 LLM 流式对话编排、系统提示词装配、云端工具执行、Cron 调度，以及通过 IPC 将本地工具调用下发给 Desktop/Runner。

设计文档：[design.md](../design.md) §1 / §2 / §5 / §6 / §7 / §9

## 架构地图

```
backend/
├── common/           # 框架基类与 API 助手（业务无关）：ModelBase/TimestampMixin · get_router · get_or_404/list_response
├── components/        # 基础设施单例 + 通用工具：database · config · logger · constants · functions · background · hashing
│   │                   + 横切基础设施（无领域逻辑）：correlation · rate_limit · redact · attachments · temp_files
├── modules/           # 按领域拆分的 ORM 模型 + Pydantic 契约：auth / conversation / companion / memory / scheduler / settings / update / ws / system
├── services/          # 服务/编排层（业务逻辑）。无 god-facade——按子包直接 import
│   ├── chat/          # 对话编排：orchestrator(run_chat_turn) · streaming · tool_dispatch · persistence · turn_inputs · heartbeat · types
│   │                   + emitter(循环断路器) · system_prompt · history · message_sanitization · think_scrubber · agent_delegate · commands
│   ├── gateway/       # WS 网关：connection(MANAGER+LISTEN/NOTIFY) · jsonrpc · emitter · ipc · runtime · auth · handlers(22 个 JSON-RPC 方法)
│   ├── llm/           # LLM 客户端与错误分类：llm_client · llm_retry · error_classifier · context_compressor · user_config
│   ├── tools/         # 工具框架 + 内置工具：registry · guardrails · memory · ... + builtin/(web/tts/image_gen/send_message/cronjob)
│   ├── companion/     # 伙伴系统：persona_service · avatar_service
│   └── scheduler/     # 后台任务：cron · title_generator · background_review
├── api/v1/            # 薄 HTTP/WS 端点，pkgutil 自动发现——chat.py(唯一 WS，仅薄端点委托 gateway/handlers) / user / sessions / llm / ...
└── main.py            # lifespan + middleware + 遍历 api.ROUTERS + 显式工具注册 import
```

依赖方向（低 → 高）：`common` / `components`（框架基座，**不** import modules/services）→ `modules`（领域模型与契约）→ `api/v1`（端点）/ `services`（服务层）→ `main.py`，无反向。`common` 放纯定义/基类（无模块级状态、无副作用）；`components` 放有状态基础设施单例（`ENGINE`、`SETTINGS`、logger 缓存）+ 无状态通用工具 + 横切基础设施（correlation/rate_limit/redact/attachments/temp_files，无领域依赖；`rate_limit` 因依赖 `modules.auth` 留在 `services/`）。`services` 无顶层 re-export facade——消费者直接 `from services.chat import run_chat_turn`，import 行即依赖图。REST 路由见 `api/v1/`（FastAPI `/docs`）；WS JSON-RPC 方法注册见 `services/gateway/handlers.py`（`api/v1/chat.py` 仅薄端点）。

**循环断路器**：`chat ↔ gateway`、`chat ↔ scheduler`、`chat → companion → tools.builtin → scheduler → chat` 三条 import 环全部经 `services/chat/emitter.py`（`Emitter` 协议，零内部依赖）收敛——`chat/__init__.py` 急切 import `emitter` + `types`，重编排器/`turn_inputs`/`agent_delegate` 经 `__getattr__` 懒加载，保证 import chat 包不会触发整张服务图。

**工具自注册**：每个工具模块在 module bottom 调 `REGISTRY.register(...)`；旧 `services` facade 的 eager re-export 曾隐式触发注册，facade 拆除后改由 `main.py` 显式 `import services.tools.builtin` / `from services.chat import agent_delegate` 触发（首条 chat turn 前完成）。

## 工具三层分类

`services/tools/registry.py` 把所有工具分为三类，决定执行位置与凭证需求：

| 类型 | 执行位置 | 判定标准 |
|------|----------|----------|
| **backend tools** | 服务端进程内 | 需要服务端资源（LLM API key、DB、外部 API 凭证）。`agent_delegate_tool` 自己 spawn 子 agent 跑完整 chat-turn（需 Backend LLM 编排）；`cronjob` 在 `services/scheduler/cron.py` 进程内调度 |
| **memory tools** | `NativeMemory.execute_tool`（注入 DB session） | 既不需要本地环境也不调外部 API，但需 Backend DB 访问 |
| **runner tools** | 通过 IPC 下发给 Runner | 需要用户本地环境（终端、文件系统、浏览器、代码执行） |

**LLM 可见性**（`CORE_TOOLS` in `services/chat/types.py`）：所有 backend + memory + 核心 runner 工具在 chat 起始时直接暴露给 LLM schema（硬保证白名单）。Runner 注入的工具经 `tools.sync` 上报后同样进入 CORE。

**Config-aware 过滤**：每个 backend tool 声明 `availability_check(user_settings) -> bool`，`get_all_schemas` 按 check 静默过滤不可用项（Predicate 异常时单 tool 静默隐藏，fail-closed）。

**陪伴语义映射**——已有 backend tools 在新定位下恰好覆盖伙伴核心能力（完整映射见 [design.md §7.4](../design.md)）：

| 工具 | 伙伴场景 |
|------|----------|
| `image_generate` | 生成专属桌面形象 |
| `text_to_speech_tool` | 伙伴语音（让伙伴"能说"） |
| `send_message_tool` | 伙伴主动发起对话（问候/提醒/闲聊） |
| `web_search` / `web_extract` | 伙伴帮用户查信息、聊时事 |
| `memory_*` | 伙伴对用户的长期记忆 |

## IPC Future 桥接

`services/gateway/ipc.py` 维护 `_pending: dict[(user_id, call_id), Future]`——键是 `(user_id, call_id)` 而非单 `call_id`：并发用户不共享 future，`user_id` 来自 JWT 解析，WS 断开时 `discard_user` 取消该用户所有未决 future。

完整工具调用流（LLM → Backend → Desktop → Runner → 回传）见 [design.md §5.2.I](../design.md)。Backend 侧的关键约束：

- **超时**：`ipc_future_timeout_seconds`（默认 300s），超时返回 synthetic error
- **快速失败**：`_dispatch_runner_tool` 做 active_connections → has_runner_tools → send_json 异常三层检查，通常 < 100ms 返回离线错误；仅绕过三层后才进入 300s 超时
- **JWT 过期边界**：token 在 IPC 飞行途中过期时 Desktop 的 `tool.result` 被 `gateway/auth` 拒绝 → future 挂起直到超时。`discard_user` 在 WS 断开时清理，但 token 过期不触发 WS 断开——当前靠超时兜底
- **通用事件分发**：`dispatch_user_event(user_id, event_type, payload)` 复用同一 future 通道发任意 JSON-RPC 事件（`reload.mcp` 用此路径），desktop 收到后转发给 Runner 并经 `tool.result` 路径回包

## WS 会话与配置

`/api/chat/ws` 每个连接持有一份 `runtime_sessions: dict[str, RuntimeSession]`，键是 `Conversation.id` 的 str 形式。

**预创建 DB 行**：`session.create` 先开 DB 行返回 ID，renderer 无需等待首次 turn 即拿到可路由标识。WS 重连后 `session.resume` 重新挂载 in-memory runtime 恢复 cwd/branch 上下文。turn 期间每 20s 发 `session.info` heartbeat 保持字段新鲜、保证 renderer `busy` 状态在每个出口清空。

**配置层级**：全局 `UserSetting` 表（可热改）+ `Settings`（pydantic-settings，env / .env，需重启）两层。`config.set` 的 `scope == "global"` 与 `session_id` **互斥**（同时给 = INVALID_PARAMS）；写入只接受 allow-listed keys，MCP server 配置不在列（存 runner 主机 `$DESKAGENT_HOME/config.yaml`，由 Desktop `deskagent:runner-config:write` 写入）。`session_id` 写 `Conversation.settings_json`（同步更新 in-memory runtime），`_merge_session_settings` 把 per-session overrides 翻译为全局 key 命名空间。

**`config.get({key:"project"})`** 返回 `{}`——filesystem-bound，由 Desktop 在 WS 请求入口处本地拦截（读 `.git/HEAD`），后端在 Docker 中看不到客户端文件系统。

## Cron 与事件下发

伙伴主动陪伴（问候、提醒、情境闲聊）经 PostgreSQL LISTEN/NOTIFY + Outbox 表支撑（[design.md §6](../design.md)）。

`services/scheduler/cron.scheduler_loop` 每 60s `_tick()`：扫描到期任务，CAS 推进 `next_run_at`（多副本安全），写 `cron.trigger` 到 `ws_events` outbox。`_tick` 不 await WS 推送——慢客户端不卡 cron 事务。PostgreSQL trigger 在 `ws_events` INSERT 时 `NOTIFY ws_events_channel`，每个 Backend 副本独立 `LISTEN` + `DELETE ... RETURNING` 原子认领消费（行锁保证不重复投递）。无效 cron 表达式自动暂停 job。

## 系统提示词与上下文管理

`services/chat/system_prompt.build_system_prompt_parts`：stable / context / volatile 三段装配。角色定义注入 stable 段（驱动伙伴说话风格与性格）。按模型族注入执行纪律（OpenAI/Gemini/Grok）。Steer 通道用 `[OUT-OF-BAND USER MESSAGE]` 标记。

消息截断（`message_sanitization.truncate_chat_history`）：保留最近 40 条非 system 消息；单条字符上限 15000。

## 错误分类管道

`services/llm/error_classifier.py` 将所有 API 层或依赖项错误收拢为 `FailoverReason`（21 种），经 8 步优先级流水线过滤：provider patterns → HTTP status → error code → message pattern → SSL/TLS transient → server disconnect → transport heuristics → unknown fallback。分类决定恢复策略（退避重试 / 凭证轮换 / 压缩上下文 / 不重试等）。

**REST 错误信封**：`/api/llm/completion` 与 `/api/media/*` 在异常路径上调 `classify_api_error`，把 `FailoverReason` + `status_code` 折成 `{error, reason, status}` 返回。原始异常（可能带 provider URL / 部分 auth header）只写服务端 log，**永远不出后端**——满足 [design.md §9](../design.md) 的 -32603 "no internal detail" 契约。

**附件 fetch 失败**：LLM 无法下载临时媒体文件时（链接过期、网络隔离），拦截 Proxy 端原始 SDK 报错，向用户返回 provider-agnostic 短消息，避免误导性触发 LLM 回退逻辑。

## 安全设计

- **Tool Reserved Keys 防注入**：`registry.execute_backend_tool` 把 `user_id`、`llm_config`、`user_settings` 标记为 reserved——LLM 塞同名 key 静默丢弃。角色定义同样受此保护，防止用户对话内容注入改写伙伴人格。
- **不可信工具结果包裹**：`web_search`/`web_extract`/`browser_*`/`mcp_*` 的字符串结果用 `<untrusted_tool_result>` 包裹。短字符串（`untrusted_wrap_min_chars`，默认 32）不包——注入风险低 + 节省 token。
- **DNS Rebinding 防护**：`send_message_tool` 出站前 `getaddrinfo` 校验目标 IP，拒绝 loopback/private/multicast。
- **Think Block 清洗**：`StreamingThinkScrubber` 流式过滤 `<think>`/`<thinking>`/`<reasoning>` 标签。
- **Secret Redaction**：`redact_sensitive_text` 36 prefix patterns + regex rules。import 时快照 `DESKAGENT_REDACT_SECRETS`（anti-tamper）。
- **API Key Fingerprinting**：`GET /api/user/model-config` 返回的 `llm_api_key` 用 `fingerprint_api_key` 派生稳定但不可逆的展示标签（如 `sk-…89`），原始 key 永不离开后端——LLM 调用走服务端路径读 DB。

## Web Providers

所有 provider 实现 `WebSearchProvider` ABC，dispatcher 通过 `user_settings` 的 `web.backend` / `web.extract_backend` 选 provider。凭证**按用户注入**（先读 `user_settings`，否则回落 `os.getenv`），避免多用户共用 env-var key。

三个 provider：**ddgs**（搜索默认，无需 key）、**brave-free**（纯搜索，免费 2000 次/月）、**tavily**（搜索+提取，`web_extract` 默认 backend，唯一实现 `extract()`）。`web_search` 缺 key 时静默回退到 ddgs（搜索始终能用）；`web_extract` 缺 key 时返回明确错误，**不回退**。`web.brave_api_key` / `web.tavily_api_key` 在 `GET /api/config` 响应里**不返回原始值**，替换为 `*_set: bool` + `*_fingerprint: str`。

## 伙伴人格与形象系统

DeskAgent 伙伴的"人格"与"形象"是跨 Backend↔Desktop 的核心契约（[design.md §7](../design.md)）。设计意图详见 design.md，此处只记 backend 侧的实现决策。

### 角色定义（Persona）

`Persona` 表存用户 onboarding 产出的结构化角色定义（JSON + 渲染好的 `system_prompt_extras` 片段），按用户维度一对一持久化。作为系统提示词 stable 段的一部分注入每次 chat turn，驱动伙伴说话风格、性格表现与主动行为倾向。角色定义是伙伴行为的**唯一真相源**——只能由用户显式发起变更（重新进入角色编辑），禁止 LLM 自行改写。

### 形象资产（AvatarAsset）

`AvatarAsset` 表存历次生成的专属形象资产（provider 返回的 URL + 元数据 + 生成状态），按用户维度持久化。Postgres partial unique index 约束"每个用户最多一条 active 记录"。`/api/companion/avatar` POST 时 Backend 据角色定义装配生图 prompt → 调 `image_generate` → 写行并置 active → 经 `dispatch_user_event` 下发"孵化"事件到 Desktop。形象生成失败时返回 `502` + 友好文案（`伙伴形象生成失败，请稍后重试`），不泄露 provider 原始错误。

### 复用映射

| 能力 | 现状 | 伙伴场景复用 |
|------|------|-------------|
| 长期记忆 | `Memory` 表 + `services/tools/memory.py` | 伙伴对用户的长期记忆 |
| 主动陪伴调度 | `CronJob` 表 + `services/scheduler/cron.py` | 伙伴定时问候/提醒 |
| 事件下发 | `WSEvent` + LISTEN/NOTIFY | 伙伴主动消息送达 Desktop |
| 生图凭证 / 执行 | `UserModelConfig.image_gen_*` + `services/tools/builtin/image_generation_tool.py` | 形象生成 |
| 伙伴语音 | `services/tools/builtin/tts_tool.py` | 让伙伴"能说" |
| 主动消息 | `services/tools/builtin/send_message_tool.py` | 让伙伴主动发起对话 |

## 限流

[slowapi](https://github.com/laurentS/slowapi)（in-memory moving window）集中实现。阈值在 `Settings` 字段暴露，运营侧可经 `.env` 调整；master switch `rate_limit_enabled` 设 False 切 no-op。受保护端点：`login`（per-IP 10/min）、`llm/completion`（per-user 60/min + per-IP 200/min 双层）、`stt`/`tts`/`image_gen`（per-user）。

**per-user key 设计**：middleware 仅做 JWT 签名校验 + 解析 `sub` stash `user_id`（不做 DB 查），handler 的 `Depends(get_current_session)` 做完整校验。伪造 token 要么签名失败（降级 per-IP）要么通过验证（拿到合法 user 的桶），无安全放大风险。

**多副本约束**：in-memory storage 意味着 N 副本 = N× 单副本有效配额，未来迁 Redis 是单点改动。**fail-open**：slowapi 内部异常不阻断请求。

## 数据库

- **引擎**：PostgreSQL（`postgresql+psycopg://`），连接池 `pool_size=20, max_overflow=10, pool_recycle=3600, pool_pre_ping=True`
- **Schema**：`ModelBase.metadata.create_all`（`common/model.py`）+ PG trigger（需手动 DDL；无 Alembic）。新增列走 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`（PG 9.6+ 幂等，不破坏已部署实例）
- **WS 事件通知**：`ws_events` 表上的 PG Trigger 在 INSERT 时 `NOTIFY ws_events_channel`，`services/gateway/connection.ws_event_loop` 经 `asyncpg` 独立连接 `LISTEN`，`DELETE ... RETURNING` 原子认领 + 派发（60s 超时兜底 GC）

## Observability

- **日志**：`components/logger.py` 集中入口 + lifespan 接管 root logger；`Settings.log_level` / `log_format` 部署可调（dev: text / prod: json）；stdout only。**脱敏责任在调用方**——logger 是基础设施层**不** import `services.*`（避免循环依赖），`orchestrator`/`streaming` 等 LLM 路径在打 log 前已跑 `redact_sensitive_text`。`extra` dict key **禁止**与 stdlib `LogRecord` 内置属性同名（命中 = `KeyError` 崩溃）
- **correlation ID**：每个 HTTP 请求经 middleware 解 `X-Request-ID`（缺则生成）写入 ContextVar；跨 `asyncio.create_task` 自动透传（CPython 3.11+），**不要** wrap
- 无 `/metrics` 端点、无 OpenTelemetry 集成

## 已知限制

| 限制 | 说明 |
|------|------|
| Runner tools 在 desktop 离线时延迟返回 | ipc.await_future 有 300s 超时；三层 fast-fail 通常 < 100ms 返回，仅绕过三层后才进入超时 |
| 并行 terminal 不可用 | Runner 端共享 LocalEnvironment 实例，快照文件不可并发写。架构决定 |
| `apply_partial` 抹除"清空"语义 | PATCH 无法用 null 清字段 |
| 形象资产 URL 有 TTL | `AvatarAsset.asset_url` 直接保存 provider 返回的 URL；Desktop 必须在收到时立刻本地缓存，过期后需重新生成 |
| `image_generate` / `text_to_speech_tool` 不参与 config-aware 过滤 | 可用性取决于 LLM provider，需调用时拿 `llm_config`；Registry 构造时无从判断 |
| WS 方法当前无 Desktop 消费方 | chat 类 JSON-RPC 方法（prompt.submit / session.* / tool.result 等）已实现且可响应，Desktop 伙伴层将消费它们——当前 companion 层尚未构建 |
