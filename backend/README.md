# Backend

云端大脑——FastAPI + PostgreSQL + JWT。承载 DeskAgent 伙伴的"人格"（角色定义 + 长期记忆）与"形象"（专属形象资产生成与下发），负责 LLM 流式对话编排、系统提示词装配、云端工具执行、Cron 调度，以及通过 IPC 将本地工具调用下发给 Desktop/Runner。

设计文档：[ARCHITECTURE.md](../ARCHITECTURE.md) §1 / §4 / §5 / §6 / §8

## 架构地图

```
backend/
├── common/ · components/    # 框架基座（基类 + 有状态基础设施单例 + 横切层 correlation/redact/attachments/temp_files），不 import modules/services
├── modules/                  # 按 domain 分包的 ORM 模型 + Pydantic 契约
├── services/                 # 业务/编排层，无 facade，按子包直接 import；rate_limit.py 因依赖 modules.auth 独居 services/ 根
│   └── llm/providers/{mimo, minimax, gemini, zhipu}/ + chat/chat_emitter.py ↔ gateway/emitter.py   # provider 自注册 + chat↔gateway import 环的收敛处
└── api/v1/ + main.py         # 薄 HTTP/WS 端点（pkgutil 自动发现）+ lifespan + 路由装配 + 工具注册触发
```

依赖方向（低 → 高）：`common` / `components`（框架基座，**不** import modules/services）→ `modules`（领域模型与契约）→ `api/v1`（端点）/ `services`（服务层）→ `main.py`，无反向。`common` 放纯定义/基类（无模块级状态、无副作用）；`components` 放有状态基础设施单例（`ENGINE`、`SETTINGS`、logger 缓存）+ 无状态通用工具 + 横切基础设施（correlation/redact/attachments/temp_files，无领域依赖）。`rate_limit` 同属横切层但因依赖 `modules.auth` 留在 `services/rate_limit.py`。`services` 无顶层 re-export facade——消费者直接 `from services.chat import run_chat_turn`，import 行即依赖图。REST 路由见 `api/v1/`（FastAPI `/docs`）；WS JSON-RPC 方法注册见 `services/gateway/handlers.py`（`api/v1/chat.py` 仅薄端点）。

**循环断路器**：`chat ↔ gateway`、`chat ↔ scheduler`、`chat → companion → tools.builtin → scheduler → chat` 三条 import 环全部经 `services/chat/chat_emitter.py`（`Emitter` 协议，零内部依赖）收敛——`chat/__init__.py` 急切 import `chat_emitter` + `types`，重编排器/`turn_inputs`/`agent_delegate` 经 `__getattr__` 懒加载，保证 import chat 包不会触发整张服务图。`gateway/emitter.py`（`JsonRpcEmitter`）import `chat.chat_emitter.Emitter`，是 chat↔gateway 环的接合点——两个 emitter 文件不要混淆。

**工具自注册**：每个工具模块在 module bottom 调 `REGISTRY.register(...)`；`main.py` 显式 `import services.tools.builtin` / `import services.chat.agent_delegate` 触发注册（首条 chat turn 前完成）。

## 工具三层分类

`services/tools/registry.py` 把所有工具分为三类，决定执行位置与凭证需求：

| 类型 | 执行位置 | 判定标准 |
|------|----------|----------|
| **backend tools** | 服务端进程内 | 需要服务端资源（LLM API key、DB、外部 API 凭证）。`agent_delegate_tool` 自己 spawn 子 agent 跑完整 chat-turn（需 Backend LLM 编排）；`cronjob` 在 `services/scheduler/cron.py` 进程内调度 |
| **memory tools** | `NativeMemory.execute_tool`（注入 DB session） | 既不需要本地环境也不调外部 API，但需 Backend DB 访问 |
| **runner tools** | 通过 IPC 下发给 Runner | 需要用户本地环境（终端、文件系统、浏览器、代码执行） |

**LLM 可见性**（`CORE_TOOLS` in `services/chat/types.py`）：所有 backend + memory + 核心 runner 工具在 chat 起始时直接暴露给 LLM schema（硬保证白名单）。Runner 注入的工具经 `tools.sync` 上报后同样进入 CORE。

**Config-aware 过滤**：每个 backend tool 声明 `availability_check(user_settings) -> bool`，`get_all_schemas` 按 check 静默过滤不可用项（Predicate 异常时单 tool 静默隐藏，fail-closed）。

**陪伴语义映射**——Backend tools 覆盖伙伴核心能力：

| 工具 | 伙伴场景 |
|------|----------|
| `image_generate` | 生成专属桌面形象 |
| `text_to_speech_tool` | 伙伴语音（让伙伴"能说"） |
| `send_message_tool` | 伙伴主动发起对话（问候/提醒/闲聊）——无 webhook 时投递 companion.message 到桌面 |
| `web_search` / `web_extract` | 伙伴帮用户查信息、聊时事 |
| `memory_*` | 伙伴对用户的长期记忆 |

## IPC Future 桥接

`services/gateway/ipc.py` 维护 `_pending: dict[(user_id, call_id), Future]`——键是 `(user_id, call_id)` 而非单 `call_id`：并发用户不共享 future，`user_id` 来自 JWT 解析，WS 断开时 `discard_user` 取消该用户所有未决 future。

完整工具调用流（LLM → Backend → Desktop → Runner → 回传）见 [ARCHITECTURE.md §4.2.I](../ARCHITECTURE.md)。Backend 侧的关键约束：

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

伙伴主动陪伴（问候、提醒、情境闲聊）经 PostgreSQL LISTEN/NOTIFY + Outbox 表支撑（[ARCHITECTURE.md §5](../ARCHITECTURE.md)）。

`services/scheduler/cron.scheduler_loop` 每 60s `_tick()`：扫描到期任务，CAS 推进 `next_run_at`（多副本安全），写 `cron.trigger` 到 `ws_events` outbox。`_tick` 不 await WS 推送——慢客户端不卡 cron 事务。PostgreSQL trigger 在 `ws_events` INSERT 时 `NOTIFY ws_events_channel`，每个 Backend 副本独立 `LISTEN` + `DELETE ... RETURNING` 原子认领消费（行锁保证不重复投递）。无效 cron 表达式自动暂停 job。

**伙伴主动消息通道**：`send_message_tool` 无 `target_webhook` 时走 companion 原生路径——`_emit_companion_message` 写 `companion.message {text}` 到 `ws_events` outbox，经同一套 LISTEN/NOTIFY 推到桌面端（伙伴 TTS + 气泡呈现，[ARCHITECTURE.md §4.1.A](../ARCHITECTURE.md)）。带 `target_webhook` 时仍是外部 webhook POST（Slack/Discord 等）。**打扰档位**：`companion.set_disturbance_tier {tier}` JSON-RPC（`services/companion/disturbance.py` 进程内 per-user 存储，默认 `normal`）——`quiet` 档时 `send_message_tool` 把消息文本吞掉，但 LLM 推理出的 affect 经 `companion.affect` 事件透传（断消息不断 affect，`services/companion/affect_emit.py::emit_companion_affect`）。**情境化 affect**：`companion.check_affect {idle_seconds, local_hour}` JSON-RPC（`services/companion/affect_check.py::check_affect`）由 Desktop idle 轮询触发，Backend 加载 persona + 最近记忆跑一次 LLM 推理，决定是否 emit `companion.affect`——触发时机由 Desktop 控制（知道真实 idle），情绪推理由 Backend LLM 承担（有 persona + 记忆）。Desktop 侧也客户端过滤，此为防御层。

## 系统提示词与上下文管理

`services/chat/system_prompt.build_system_prompt_parts`：stable / context / volatile 三段装配。角色定义注入 stable 段（驱动伙伴说话风格与性格）。角色定义存在时同步注入 affect 指令（`COMPANION_AFFECT_GUIDANCE`，要求 LLM 在文字回复前缀 `[affect:EMOTION]` 标签，由 `AffectScrubber` 在流式路径剥离并附加到 `message.complete`）。按 provider 声明的 `PROMPT_FAMILY`（`BaseProvider` ClassVar，默认 `"openai"`，Gemini 覆写为 `"google"`）选择执行纪律 guidance 块；`turn_inputs` 在装配 `AgentPromptConfig` 时从 `provider_for_service` 实例读取该值传入。Steer 通道用 `[OUT-OF-BAND USER MESSAGE]` 标记。

消息截断（`message_sanitization.truncate_chat_history`）：保留最近 40 条非 system 消息；单条字符上限 15000。

## 错误分类管道

`services/llm/error_classifier.py` 将所有 API 层或依赖项错误收拢为 `FailoverReason`，分类决定恢复策略（退避重试 / 凭证轮换 / 压缩上下文 / 不重试）。

**REST 错误信封**：`/api/llm/completion` 与 `/api/media/*` 在异常路径上调 `classify_api_error`，把 `FailoverReason` + `status_code` 折成 `{error, reason, status}` 返回。原始异常（可能带 provider URL / 部分 auth header）只写服务端 log，**永远不出后端**——满足 [ARCHITECTURE.md §8](../ARCHITECTURE.md) 的 -32603 "no internal detail" 契约。

**附件 fetch 失败**：LLM 无法下载临时媒体文件时（链接过期、网络隔离），拦截 Proxy 端原始 SDK 报错，向用户返回 provider-agnostic 短消息，避免误导性触发 LLM 回退逻辑。

## LLM Provider 抽象

`services/llm/providers/` 在五类服务（`ChatProvider` / `ImageGenProvider` / `VideoGenProvider` / `TTSProvider` / `STTProvider`）各放一个 ABC，`BaseProvider` 公共根。**Provider 名永远显式**：tier 1 用户配置存 JSON 时就带 `name`，全局配置读 `SETTINGS.<svc>_provider` / `SETTINGS.providers`，两者皆空则回落到 `SERVICE_DEFAULT_PROVIDER[svc]`——chain resolver 不会从 `base_url` host 反推。注册表 `registry.register(service_type, provider_name, cls)` 由 provider 子包 `__init__.py` 自注册；`providers/__init__.py` `from . import mimo, minimax, gemini, zhipu` 触发。哪些 provider 支持哪些能力由 `(ServiceType, name)` 在 `_REGISTRY` 里的存在与否决定——`providers_supporting(service)` 返回所有注册过此服务的 provider 名列表。

### PROVIDER-first 配置

`PROVIDERS` env（逗号分隔）声明可用 provider 的优先级顺序。每个 provider 有自己的 `{NAME}_API_KEY` 和可选 `{NAME}_BASE_URL`，覆盖在该 provider 涉及的每个能力上。**provider 自带的默认 MODEL_NAME** 通过每个 provider 类的 `DEFAULT_MODELS: ClassVar[dict[str, str]]` 声明，注册时 `registry.register()` 同步进 `_PROVIDER_DEFAULT_MODELS`——resolve 链不需 import 每个 provider 类就能拿到默认 model。

两套体系**共存**（用户明确要求）：
- `PROVIDERS` + `{NAME}_API_KEY` 是面向"一个 provider 覆盖多种能力"的统一凭证
- 老的 `<svc>_PROVIDER` / `<svc>_API_KEY` / `<svc>_BASE_URL` / `<svc>_MODEL_NAME` 仍是面向"某个具体能力直接配凭证"的定向凭证，**优先级更高**

`resolve_provider_chain(db, user_id, svc)` 构建有序回落链，首个 key+base_url 齐全的 provider 生效。Tier 1（用户 provider）prepend 到既有 fold-in 链前，按 `provider_name` 去重（tier 1 胜）：
1. **用户 provider**（最高优先级）— `UserModelConfig.provider_config`（JSON 有序列表 `[{name, api_key, base_url}]`，按 `user_id` 过滤）。JSON 形状，新增 provider 家族无需改 schema
2. 用户能力凭证 — `UserModelConfig.<svc>_base_url` / `<svc>_api_key` / `<svc>_model_name`
3. 全局 provider — `SETTINGS.providers` / `<svc>_provider` 软重排 + `SETTINGS.<NAME>_API_KEY` / `<NAME>_BASE_URL`
4. 全局能力凭证 + built-in 默认 — `SETTINGS.<svc>_*` → `PROVIDER_DEFAULT_URLS[provider][service]` / `default_model_for(provider, service)`

Tier 2-4 保持原 fold-in 语义（per-cap 覆盖 provider-level），兼容老的单 key 部署（`LLM_BASE_URL`+`LLM_API_KEY`+`LLM_MODEL_NAME`）。无用户上下文（`db`/`user_id` 为 None）时仅走 tier 3-4。admin upsert 对 provider_config 的空 `api_key` 做"保留现有值"合并（admin 看不到原始 key）。

`<svc>_PROVIDER` 不参与单 slot 凭证解析；它**只软重排** chain——把命名的 provider 提到第一位，chain 仍保留其它 provider 作为 fallback（设计决策：用户希望链不塌缩）。空 `<svc>_provider` + `PROVIDERS` 未设 → `SERVICE_DEFAULT_PROVIDER[svc]` 兜底（chat/stt/tts→`mimo`、image/video→`minimax`），chain 退化为单元素。

**provider 默认 URL**（`PROVIDER_DEFAULT_URLS`）：MiMo 含 `/v1`（OpenAI SDK 需完整 base_url）；MiniMax 仅 chat（llm）URL 含 `/v1`，其余能力（tts/image_gen/video_gen）的路径已自带 `/v1`、base_url 不含；Gemini chat 含 `/v1beta/openai/`（Google OpenAI 兼容端点），其余能力（stt/tts/image_gen）用 `generativelanguage.googleapis.com` 原生端点，`video_gen` 为空字符串（Veo 需 Vertex AI，暂不支持）。**key 隔离**：minimax provider 始终用 `MINIMAX_API_KEY`，不继承 MiMo key（host 不同会 401）；gemini provider 用 `GEMINI_API_KEY`（兼容 `GEMINI_KEY` 别名）；其他 provider 链路最终回落 `LLM_API_KEY` 兼容老部署。`MIMO_KEY` / `MINIMAX_KEY` / `GEMINI_KEY` 是 legacy 别名。

`backend/.env.example` 是完整模板。**最小配置**：`LLM_API_KEY` + `MINIMAX_API_KEY`——每 provider 用自己的默认 URL + model。

### Fallback chain（`services/llm/llm_fallback.py`）

`execute_with_fallback(db, user_id, service_type, call_fn, *, stream_started=None)` 在 `resolve_provider_chain()` 返回的有序 `ProviderConfig` 列表上迭代 `call_fn(provider)`；某 slot 抛错时按 `ClassifiedError.should_fallback` 决定 continue 到下一 provider 还是 raise（让 HTTP envelope `api/v1/_http_errors.py` 走标准 `{error, reason, status}` 路径）。

**触发 fallback 的分类原因**（`error_classifier.should_fallback` 已设 True）：`auth`、`billing`、`rate_limit`（持续性）、`model_not_found`、`format_error`、`content_policy_blocked`。**不触发**（留在 per-provider retry 层）：`overloaded`、`server_error`、`timeout`、`context_overflow`、`payload_too_large`——`call_with_retry` 的退避循环负责。

**流式 chat 特殊处理**：`_stream_llm_response` 新增 `on_first_chunk: Callable[[], None]` 参数，第一块 chunk 处理后回调一次；调用方通过 `stream_started=lambda: <flag>` 传给 dispatcher。一旦首个 chunk 已发出到 renderer，任何 provider 失败**不再 fallback**——用户已经看到部分输出，切换 provider 只会造成 transcript 截断。

**video_gen fallback 范围**：仅 `provider.submit()` 走 chain；poll/fetch 钉在持有 `task_id` 的 provider（task_id 跨 provider 不可迁移）。submit 成功后若 chain 跳到下一个 provider，`VideoGenJob.provider` 字段会同步更新。

### 三层入口

- `provider_for_service(db, user_id, service_type) -> BaseProvider`：单 provider 入口，返回 chain[0]；老 call site（直读 `provider.generate()` 等）继续工作
- `client_for_service(...) -> (AsyncOpenAI, model)`：OpenAI 兼容 shim，对非 OpenAI provider（MiniMax video）抛 `MissingLlmConfigError`
- `execute_with_fallback(...)`：新 chain-aware 入口，handler（REST / tool / video_jobs submit）用它做自动降级

### 关键设计决策

- 非 OpenAI 协议的能力**一律走** `providers/http.py` 的 httpx 池（base_url + api_key 缓存，超时 `llm_request_timeout_seconds`），不做 `AsyncOpenAI.post` 兼容层 hack
- `ProviderError(status_code, body, provider, model)` 字段名刻意对齐 `error_classifier._extract_status_code/_extract_error_body`，让 `classify_api_error` 复用既有分类流水线——MiniMax `base_resp.status_code` 经 `providers/minimax/_errors.py` 翻译后复用既有分类流水线
- MiniMax key 不能继承 MiMo key（host 不同 401）；resolver 在 provider=minimax 时强制把回落链截断在 `minimax_api_key`

**为什么 MiMo 没有 `_errors.py`**：MiMo chat.completions 协议就是 OpenAI 标准格式，错误返回 OpenAI 标准的 `{"error":{"message":"..."}}` HTTP 状态码，`AsyncOpenAI` 自动解析成 `APIStatusError`（带 `.status_code` 与 `.body`），`error_classifier` 直接读这俩字段。MiniMax 不一样——错误是 HTTP 200 外层 + `base_resp.status_code` 内层，SDK 看不到，所以需要单独翻译。

## 视频生成

MiniMax Hailuo 异步三段式：`POST /v1/video_generation`（task_id）→ `GET /v1/query/video_generation?task_id=...` 轮询 → `GET /v1/files/retrieve?file_id=...` 拿到 9 小时有效的 `download_url`——必须在 provider 文件过期前**立即**下载落盘，否则前端 404。`services/media/video_jobs.py` 把这条流水编排成：

- `enqueue_video_job` 入库 + 提交 + `asyncio.create_task` 起后台 polling
- `_poll_and_finalize` 每 `video_gen_poll_interval_seconds`（5s）查一次，最长 `video_gen_max_poll_seconds`（900s）
- succeeded 后 `provider.fetch` → httpx streaming 下载（`video_gen_download_max_bytes=200MB` 上限）→ `components.save_file` 落 `data_dir/temp-media`，对外只暴露自家 `/api/media/files/<id>` URL
- 写 `WSEvent(user_id, "video_gen.completed"|"video_gen.failed", payload)`，复用 `services/gateway/connection` 的 LISTEN/NOTIFY outbox 推到桌面端

**REST 接口**：
- `POST /api/media/video_gen`（rate-limit 3/min）：返回 **202** `{task_id, status, poll_url}`；可选 `wait_seconds=0..60` 做有限伪同步（完成的化 200 直接带 `url`）
- `GET /api/media/video_gen/{task_id}`：返回 `{status, url|null, error|null, created_at, updated_at}`，按 `user_id` 过滤

**进程恢复**：lifespan 启动时调 `resume_pending_video_jobs()` 把 `status IN ('queued','processing')` 的行重新挂上 polling 任务——deploy / OOM / SIGTERM 不会丢失在飞的视频。

**Tool**：`video_generate`（schema: prompt/duration/resolution/first_frame_image/aspect_ratio）+ `video_generate_status`（schema: task_id）。前者最多等 `video_gen_tool_wait_seconds`（180s）；超时返回 `{success:true, pending:true, task_id, hint:"用 video_generate_status 查询"}`——后台任务继续跑。MiniMax 不暴露 ASR，所以 `stt` provider 没有 `minimax` 实现。

**伙伴层 clip 的种子图**：`companion/avatar_service` 生成角色动画 clip 时，`first_frame_image` 固定为该用户当前 portrait，prompt 描述场景/动作——所有 clip 共享同一颗种子图以保证跨 clip 角色一致；portrait 重生时全部 clip 失效重排（[ARCHITECTURE.md §6.2](../ARCHITECTURE.md)）。clip 通过 `clip.updated` 事件（payload 携 scene + tier）单通道下发，`enqueue_video_job(..., emit_event=False)` 抑制通用的 `video_gen.completed/failed` 避免双通知。

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

DeskAgent 伙伴的"人格"与"形象"是跨 Backend↔Desktop 的核心契约（[ARCHITECTURE.md §6](../ARCHITECTURE.md)）。设计意图详见 ARCHITECTURE.md，此处只记 backend 侧的实现决策。

### 角色定义（Persona）

`Persona` 表存用户 onboarding 产出的结构化角色定义（JSON + 渲染好的 `system_prompt_extras` 片段），按用户维度一对一持久化。作为系统提示词 stable 段的一部分注入每次 chat turn，驱动伙伴说话风格、性格表现与主动行为倾向。角色定义是伙伴行为的**唯一真相源**——只能由用户显式发起变更（重新进入角色编辑），禁止 LLM 自行改写。

**onboarding 断点恢复**（design §6.3）：onboarding 逐字段增量持久化经两个 JSON-RPC 方法：`onboarding.get_state` 返回已采集字段 + 下一个未答问题（`next_field`）；`onboarding.submit {field, value}` 即时落盘单个字段。Desktop 启动时调 `get_state`，未完成则从 `next_field` 恢复，崩溃/退出不丢进度。draft 存在 `Persona.definition_json`（`is_complete=False`），完成后 Desktop 发 `PUT /api/companion/persona` 覆盖为最终角色定义（`is_complete=True`）。

**13 步 onboarding + 角色/用户单 PUT 双写**：onboarding 采集 13 个字段（4 旧 + 3 角色 —— `species` / `character_gender` / `appearance` —— + 5 结构化用户字段 `user_call_name` / `user_gender` / `user_age_bucket` / `user_hobbies` / `user_freeform`）。`PersonaUpdate` schema 把 user_* 字段显式声明为 optional 仍在 `extra="forbid"` 严格校验下；`update_persona` 在 `_validate_definition(persona_def)` 之前先把 user_* 字段抽出交给 `services.companion.memory_bootstrap.record_user_profile` 落到 `Memory` 表（query-then-update 幂等 upsert，tags `["onboarding","user_profile"]`，context `user_profile:*`，与 `NativeMemory._retain` 同一模式保证 SQLite 单测与生产 Postgres 同行为），然后写 persona 字段 + `is_complete=True`，**同一 `db.commit()` 让两路写入具备原子性**——失败要么都回滚、要么都落。生产 Postgres `main.py::_install_schema_extensions` 加 `uq_memories_user_context` 部分唯一索引做并发 race 兜底。avatar 生图 prompt 经 `_SPECIES_EN` / `_GENDER_EN` 查表把中文物种/性别翻成稳定英文 token（`灵兽` → `spirit beast`、`女` → `female`…），未知值/自由输入原文回退保留用户原意。

### 形象资产（AvatarAsset）

`AvatarAsset` 表存历次生成的专属形象资产（provider 返回的 URL + 元数据 + 生成状态），按用户维度持久化。Postgres partial unique index 约束"每个用户最多一条 active 记录"。`/api/companion/avatar` POST 时 Backend 据角色定义装配生图 prompt → 调 `image_generate` → 写行并置 active → 自动从新 portrait 种子 batch-0 idle clip。形象生成失败时返回 `502` + 友好文案（`伙伴形象生成失败，请稍后重试`），不泄露 provider 原始错误。

**avatar.regenerate** JSON-RPC：带可选 `feedback` 文本（如"头发长一点"），折入 prompt 做增量重生成。旧 clip 在新 portrait 成功后才失效（避免生图失败时用户失去全部 clip），然后 batch-0 重新排队。

### 动画 clip 流水线（AvatarClip）

`AvatarClip` 表存以 portrait 为种子的图生视频 clip，每个 clip 绑定一个 `scene` 标签（与 Desktop 动画状态机状态对齐：`idle` / `speaking` / `thinking` / `happy` / ...）。scene 目录 + 批次优先级定义在 `services/companion/clip_service.CLIP_SCENES`。

- **渐进式生成**：batch 0（idle）在 portrait 生成时同步排队；其余批次（speaking/thinking/working → 生命周期 → 情绪变体）后台渐进生成
- **事件下发**：clip 通过 `clip.updated` 单通道下发（payload 携 scene + tier + url + keyframe_url + keyframe_meta + status），companion 服务以 `enqueue_video_job(..., emit_event=False)` 抑制通用 `video_gen.*` 事件避免双通知；`avatar.list_clips` 是拉取式同步入口（首次启动 / 断线重连补齐）
- **avatar.list_clips** JSON-RPC：返回全部 clip + 实时生成状态（JOIN `VideoGenJob` 行）
- **衍生失效**：portrait 重生时所有 clip 失效（design §6.2——同一颗种子图从机制上保证跨 clip 一致性，跨版本不可复用）

### 音色匹配（voice catalog）

onboarding 的音色偏好（`voice` 草稿字段）经 JSON-RPC 落到具体 voice id（COMPANION_DESIGN.md §4.2 / §4.5）：

- **`tts.list_voices`**：返回当前用户激活的 TTS provider 的候选音色目录（`{provider, voices:[{id,label,gender,language,tags,description}], supports_voice_design, voice_design_guide}`）。voice id 是 provider 私有的；`language`（`"zh"`/`"en"`/`"multi"`）驱动语言偏好匹配。`supports_voice_design` 标记该 provider 是否支持用户自定义音色设计，`voice_design_guide` 是写法指南文本供前端展示。**列表按 `language` 排序**对应"默认中文"的产品方向（[runner/README.md §音频工具](../runner/README.md#音频工具-stt--tts)）。
  - **可选 `language` 过滤**（`{language: "zh"}` / `{"language": "en"}` / `{"language": "multi"}`）：返回仅该语言的子集。未知 / 空字符串值走全量未过滤路径。用于 voice picker UI 的"中文 / English / 全部"tabs——后端过滤避免前端在 `voices` 数组上做二次筛选，让 render-side 的 catalog 始终是策划好的 zh-first 顺序。Filter 后 default_voice 退化到第一个匹配的 voice；过滤后空则回退到 `DEFAULT_VOICE` 兜底，shape 永远存在。
- **`tts.match_voice {preference}`**：把自由文本偏好（"温柔的少女音"）经标签/性别/语言评分映射到目录中最贴合的 voice id，返回 `{provider, voice, alternatives[]}`。评分是即时确定性的——onboarding 不该为一个窄域标签任务付 LLM 延迟。无匹配时优先中性默认音色。
- **`GET /api/companion/voices`**：`tts.list_voices` 的 REST 镜像（可选 `?language=zh|en|multi`，未知值走全量）。framed 工具窗口（hub）无 WS gateway，调不到 JSON-RPC，其音色目录页经此 REST 端点拉取同一目录（复用 `list_tts_voices`）。**只读浏览**，不改音色。
- **`tts.design_voice {prompt, preview_text?}`**：用户手动描述期望音色，provider 返回 `{voice_id, trial_audio_base64, trial_audio_mime}` 供试听。设计出的 voice_id 用法与预设音色一致（客户端存储，`POST /api/media/tts` 的 `voice` 参数透传）。MINIMAX 返回稳定可复用的 voice_id（一次设计后续免费复用）；MIMO 无可复用 id，voice_id 编码为 `mimo_voicedesign:<prompt>` 自描述 token，`synthesize()` 检测该前缀后切换到 voicedesign 模型。

音色目录由各 TTS provider 类的 `VOICE_CATALOG` 类属性声明（与 `synthesize()` 同文件，保证 id 与 provider 实际接受的一致）；`services/companion/voice_catalog.py` 经 `resolve(ServiceType.tts, provider_name)` 读取该属性，提供 `VoiceEntry` 包装 + 标签评分匹配。匹配到的 voice id 由 Desktop 持久化，后续 `POST /api/media/tts` 的 `voice` 参数直接透传给 provider；未传 voice 时 call_fn 内按 `default_voice_id(p.provider_name)` 取各 provider 自己的目录首项（fallback 链中每家 provider 有独立默认，voice id 不跨 provider 通用）。

**已知限制**：

- **MIMO 设计音色按调用计费**：MIMO voicedesign 模型不返回可复用 voice_id，voice_id 编码为 `mimo_voicedesign:<prompt>` 自描述 token。每次 `synthesize()` 检测到该前缀都会切换到 voicedesign 模型重新生成，产生额外延迟和成本。Desktop 应缓存重复文本的合成结果避免重复调用。

### 伙伴情绪（affect）

Backend 在对话响应的 `message.complete` 帧内联 `affect: {emotion}` 字段（design §6.3 inline affect 原则）。实现：当角色定义存在时，系统提示词注入 affect 指令（`COMPANION_AFFECT_GUIDANCE`），要求 LLM 在每条文字回复前缀 `[affect:EMOTION]` 标签。`services/chat/affect.AffectScrubber` 在流式路径中剥离该标签（用户不可见），捕获的 emotion 附加到 `message.complete`。emotion 词汇表在 `ALLOWED_EMOTIONS`（有限枚举，可扩展但需同步 Desktop clip 目录）。

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
| 形象资产 URL 有 TTL | 持久路径 ``companion-avatars/<id>.<ext>`` + 读时 5 分钟 HMAC 签名（`X-Signed-Url-Expiry`）。`verify_signed_asset_request` 强制校验，缺签名 401。Desktop 收到签名 URL 时本地缓存，过期后走 `/api/companion/avatar/list` 重新拉 |
| `image_generate` / `text_to_speech_tool` / `video_generate` 不参与 config-aware 过滤 | 可用性取决于 provider；调用时按 `llm_config` 拉 `provider_for_service` |
| MiniMax 文件 URL 9 小时有效期 | video_gen `provider.fetch` 拿到的 `download_url` 仅 9 小时有效，必须立即下载落 `data_dir/temp-media`，**不能**直接返给前端 |
| MiniMax 内容风控 1027 不重试 | `base_resp.status_code=1027` 映射到 `content_policy_blocked` 且 `retryable=False`，避免重试三次白烧配额 |
| clip 生成依赖 video_gen provider | `AvatarClip` 排队需 video_gen 配置（默认 MiniMax Hailuo）；未配置时 clip 排队静默失败（有日志），portrait 仍正常生成 |
| clip 的 first_frame_image 需公网可达 | portrait `asset_url` 作为图生视频种子图传给 provider；本地 `/api/media/files/<id>` URL 在 Docker 内网不可达，需正确配置 `PUBLIC_URL_PREFIX` |
| IPC future 跨副本 race | `_disturbance` 表/companion_submission_id 锁、escalation `video_next_retry_at` 时间戳 CAS — 都在 process-local 内存里，多副本部署下同一用户可能被两副本同时打 CAS。架构决定：当前是单副本；多副本迁移路径见 `services/gateway/ipc.py` 顶部 TODO |
| Cron 跨副本 race | `_kick_autonomous_turn` 守卫只在 dispatcher 表里查 "用户在线"，多副本部署下两副本都会触发。当前 `kick` 内部 `is_quiet` 守卫 + per-user `asyncio.Lock` 兜底，**单副本**安全；多副本需要 LISTEN-NOTIFY 路径（见 category 8 backend README） |
| video_gen 9h URL 过期 | `provider.fetch` 拿到的 `download_url` 仅 9 小时有效；下载测试必须确保 9h 内落地；CI fixture 用 fake provider 跳过此路径 |

## MiniMax 注意事项

仅与启用 MiniMax provider 的部署相关（默认配置即开）：

- **国内 / 国际双域名**：`https://api.minimaxi.com/v1`（国内）+ `https://api.minimax.io/v1`（国际）。切换域名单独改对应 `*_base_url` env 即可。注：MiniMax llm 端用 OpenAI SDK 需要 `/v1` 后缀；其它能力（tts / image_gen / video_gen）的路径已自带 `/v1`，`_provider_level_url` 会自动剥掉 env 末尾的 `/v1` 避免 `/v1/v1/...` 404
- **API Key 单源**：`MINIMAX_API_KEY` 是 chat/image/video/tts 通用 key；`MINIMAX_API_KEY` 与 `LLM_API_KEY`（MiMo）相互独立。**不要**让 MiniMax 配置继承 MiMo key（host 不同会 401）——`resolve_provider_config` 在 provider 推断为 minimax 时强制把回落链截断在 `minimax_api_key`
- **单 key 多能力计费**：image-01 按张、Hailuo 按秒/分辨率、speech-2.8 按字符。`media_video_gen_rate_limit_per_minute=3` 是按秒计费的限流保守默认
- **视频下载大小上限**：`video_gen_download_max_bytes=200MB` 兜底，避免 provider 返回巨型文件撑爆 `data_dir`；1080P/10s 实测 20–60 MB
- **桌面端必须独立缓存**：服务端已 download 落本地（`/api/media/files/<id>`），不要把 MiniMax 原 URL 透传到桌面
- **ASR 不在 MiniMax 公开 API**：stt 仍走 MiMo（input_audio chat completions 扩展），未来要切其他 ASR provider 时直接挂一个新 provider 类到 `(ServiceType.stt, "<name>")` registry slot
## Provider 客户端生命周期

`services/llm/providers/http.py` 的 httpx 池与 `openai_compat` 的 `AsyncOpenAI` 客户端按 `(base_url, api_key)` 缓存，shutdown 时统一由 `services.llm.aclose_all()` 关闭。`main.py` lifespan 的 `finally` 调 `services.media.aclose_all()`（video gen 是该 httpx 池的主要消费方），后者委托 `services.llm.aclose_all`——滚动发布时释放连接池 / 文件描述符，而非等内核回收进程。`aclose_all` 因此是 `services.llm` 的公共 lifecycle 出口。
