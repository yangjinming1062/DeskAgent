# Backend

云端大脑——FastAPI + PostgreSQL + JWT。承载 DeskAgent 伙伴的"人格"（角色定义 + 长期记忆）与"形象"（专属形象资产生成与下发），并负责 LLM 流式对话编排、系统提示词装配、云端工具执行、Cron 调度，以及通过 IPC 将本地工具调用下发给 Desktop/Runner。

设计文档：[design.md](../design.md) §1 / §2 / §5 / §6 / §7 / §9

## 模块结构

```
backend/
├── main.py              # FastAPI 入口 + lifespan
├── config.py            # pydantic-settings 全局配置
├── constants.py         # 集中可调常量（阈值、限制、协议码）
├── logger.py            # 统一日志入口: get_logger + setup_logging + _JsonFormatter
├── models.py            # SQLAlchemy 10 张表
├── schemas.py           # Pydantic 通信契约
├── utils/               # 通用工具集
│   ├── auth.py              # JWT 签发/校验 + get_current_session 依赖
│   ├── db.py                # engine / SessionLocal / Base
│   ├── hashing.py           # hash_password / verify_password
│   ├── json_helpers.py      # apply_partial 等 JSON 处理 helper
│   ├── text.py              # fingerprint_api_key / 文本工具
│   ├── types.py             # 辅助类型定义
│   └── background.py        # 后台任务 helper
├── core/                # 核心认知层
│   ├── __init__.py          # 统一 re-export 整个 core 子树的公共 API
│   ├── attachments.py       # Remote-mode 附件落盘 / GC
│   ├── auth_helpers.py      # WS JWT 鉴权
│   ├── correlation.py       # X-Request-ID 接入: correlation_id_middleware
│   ├── rate_limit.py        # slowapi 限流：limiter + JWT-stash 中间件 + 429 处理器
│   ├── redact.py            # 敏感文本脱敏
│   ├── slash_commands.py    # /model /yolo /reasoning 等命令实现
│   ├── temp_files.py        # 临时文件存储管理（保存、获取、清理）
│   ├── chat/                # 对话编排
│   │   ├── chat_service.py      # chat-turn 编排（run_chat_turn）
│   │   ├── chat_emitter.py      # 事件发射协议（WSEmitter / HeadlessEmitter）
│   │   ├── system_prompt.py     # 系统提示词装配
│   │   ├── message_sanitization.py  # 消息清理与截断
│   │   ├── think_scrubber.py    # Think block 流式清洗
│   │   ├── agent_delegate.py    # 子 agent 编排
│   │   └── history.py           # session.resume 消息重建
│   ├── llm/                 # LLM 客户端与错误处理
│   │   ├── llm_client.py        # LLM 客户端缓存（lru_cache）
│   │   ├── llm_retry.py         # LLM 调用重试
│   │   ├── error_classifier.py  # 结构化错误分类
│   │   ├── context_compressor.py # LLM 摘要压缩
│   │   └── user_config.py       # 用户 LLM 配置解析
│   ├── ws/                  # WebSocket 与 IPC
│   │   ├── connection_manager.py # WebSocket 连接管理
│   │   ├── ipc.py               # IPC future 桥接 + dispatch_user_event
│   │   ├── jsonrpc_dispatcher.py # WS JSON-RPC 请求/响应/事件分发
│   │   ├── jsonrpc_emitter.py   # raw frame → JSON-RPC envelope 翻译
│   │   └── runtime_sessions.py  # WS per-connection runtime 字典
│   ├── tools_runtime/       # 工具执行框架
│   │   ├── registry.py          # 工具三层注册表
│   │   ├── memory.py            # NativeMemory 执行
│   │   ├── model_tools.py       # 工具参数类型强转
│   │   ├── tool_guardrails.py   # 工具循环防护
│   │   ├── tool_dispatch_helpers.py  # 并行分批 + untrusted wrapper
│   │   ├── tool_result_classification.py
│   │   ├── file_safety.py       # dispatch 到 runner 前的路径拦截
│   │   ├── extract_provider.py  # web_extract provider 解析
│   │   ├── search_tools_tool.py # 工具搜索
│   │   └── web_providers/       # 搜索引擎插件（tavily/ddgs/brave-free）
│   ├── backend_tools/       # 云端工具实现（自注册）
│   │   ├── web_tools.py         # web_search + web_extract
│   │   ├── cronjob_tools.py     # cron CRUD
│   │   ├── image_generation_tool.py  # DALL-E 3（伙伴形象生成 + 通用图片）
│   │   ├── tts_tool.py          # OpenAI TTS（伙伴语音）
│   │   └── send_message_tool.py # webhook POST（伙伴主动消息 + is_safe_outbound）
│   ├── companion/         # 伙伴人格与形象系统（设计文档 §7）
│   │   ├── persona_service.py   # Persona CRUD + 角色定义验证 + system_prompt_extras 渲染
│   │   └── avatar_service.py    # 伙伴形象生成编排（persona → prompt → image_gen → AvatarAsset）
│   └── async_jobs/          # 后台异步任务
│       ├── background_review.py # 后台记忆提取
│       ├── cron.py              # Cron 调度（60s tick）
│       └── title_generator.py   # 会话标题自动生成
├── routers/             # HTTP/WS 端点
│   ├── _http_errors.py  # classify_api_error → HTTPException 转换 helper
│   ├── chat.py          # WS /api/chat/ws（唯一 WS 通道）
│   ├── user.py          # 用户认证 + settings
│   ├── admin.py         # 管理后台 CRUD
│   ├── sessions.py      # 会话 CRUD + 搜索
│   ├── config.py        # 用户配置 CRUD
│   ├── llm.py           # 反向 RPC 代理
│   ├── media.py         # STT / TTS / 图片生成
│   ├── companion.py     # 伙伴 onboarding：persona CRUD + 形象生成/查询/历史
│   ├── insights.py      # 用量统计
│   ├── status.py        # 用户状态快照
│   ├── update.py        # 客户端自动更新
│   ├── health.py        # GET /api/health
│   └── page.py          # 静态页面 + admin SPA
├── static/              # 管理后台 SPA（admin.html）
└── updates/             # 桌面客户端安装包
```


## API 端点总览

所有路由前缀 `/api`（`settings.api_prefix`）。

### REST

| 路径 | 方法 | 用途 | 来源 router |
|------|------|------|-------------|
| `/api/health` | GET | 健康检查 | health.py |
| **用户认证** | | | |
| `/api/user/login` | POST | 登录（password + client_version + client_context；限流：per-IP 10/min） | user.py |
| `/api/user/logout` | POST | 登出（当前 jti） | user.py |
| `/api/user/change-password` | POST | 改密码 | user.py |
| `/api/user/refresh` | POST | 刷新会话（撤销旧 jti，签发新 jti，复用当前 client_context） | user.py |
| `/api/user/model-config` | GET | 合并 LLM 配置（DB → env fallback）。**只返回 fingerprint（`sk-…89`）和 `llm_api_key_set` 布尔**，原始 key 不出后端 | user.py |
| **管理后台** | | | |
| `/api/admin/login` | POST | 管理员登录 | admin.py |
| `/api/admin/users` | GET/POST | 用户列表/创建 | admin.py |
| `/api/admin/users/{id}` | GET/PATCH/DELETE | 用户操作 | admin.py |
| `/api/admin/users/{id}/toggle-active` | PATCH | 切换激活 | admin.py |
| `/api/admin/model-configs` | GET | 模型配置列表 | admin.py |
| `/api/admin/{id}/model-config` | PUT/DELETE | 模型配置操作 | admin.py |
| **会话** | | | |
| `/api/sessions` | GET | 会话列表（分页，archived 三态；`include_subagents` 默认 `false` 隐藏子代理会话，`true` 显式展开；`order` 用 `Literal["recent","oldest"]` 校验） | sessions.py |
| `/api/sessions/search` | GET | 搜索会话（title / conv.id / 任意 message.content；q 必填，空/缺失返回 422；不受 `include_subagents` 影响——子代理始终可搜索） | sessions.py |
| `/api/sessions/{id}/messages` | GET | 获取消息 | sessions.py |
| `/api/sessions/{id}` | PATCH | 重命名/归档 | sessions.py |
| `/api/sessions/{id}` | DELETE | 删除会话 | sessions.py |
| **配置** | | | |
| `/api/config` | GET/PUT | 用户配置（扁平化 key-value） | config.py |
| `/api/config/defaults` | GET | 默认配置形状 | config.py |
| **Media** | | | |
| `/api/media/stt` | POST | 语音转文字（Whisper；≤24MB 上限，超限返 413；限流：per-user 20/min） | media.py |
| `/api/media/tts` | POST | 文字转语音（流式；≤4000 chars 上限，超限返 413；限流：per-user 30/min） | media.py |
| `/api/media/image_gen` | POST | 图片生成（DALL-E 3；限流：per-user 10/min） | media.py |
| `/api/media/files/{file_id}` | GET | 无鉴权的临时媒体文件服务（直接返回本地的图像附件 URL，供 LLM 下载理解） | media.py |
| **伙伴 onboarding（设计文档 §7）** | | | |
| `/api/companion/persona` | GET / PUT | 获取/更新伙伴角色定义（onboarding 表单）；PUT 时 `system_prompt_extras` 即时重渲染 | companion.py |
| `/api/companion/persona/extras` | GET | 单独拿 system_prompt 注入片段（供 renderer 调试） | companion.py |
| `/api/companion/avatar` | GET | 获取当前激活形象资产；不存在 → `null`，renderer 显示"蛋"占位 | companion.py |
| `/api/companion/avatar/history` | GET | 历史形象资产列表（最多 20 条） | companion.py |
| `/api/companion/avatar` | POST | 生成新形象（persona → prompt → image_gen → 写 AvatarAsset 行，置 active）；失败时对外返回 `502` + 友好文案，不泄露 provider 原始错误 | companion.py |
| **其他** | | | |
| `/api/llm/completion` | POST | LLM completion 代理（反向 RPC；错误走 `error_classifier.classify_api_error`，返回 `{error, reason, status}`，原始异常仅入服务端 log；限流：per-user 60/min + per-IP 200/min 双层） | llm.py |
| `/api/status` | GET | 用户状态快照 | status.py |
| `/api/insights/overview` | GET | 用量统计（含 `models` 配置、`platforms` client_version 分布、`skills` memory 摘要、`activity` 日级时间序列） | insights.py |
| `/api/update/*` | * | 客户端自动更新 (desktop 二进制 + Python runner; `/api/update/latest-runner.yml` 单拉 runner manifest) | update.py |

### WebSocket

唯一通道：`WS /api/chat/ws?token=<jwt>`，全量 JSON-RPC 2.0。信封规则、请求/响应/事件示例见 [design.md §3.1](../design.md)。

#### JSON-RPC 2.0 方法（已实现）

| 方法 | 响应 `result` | 实现 |
|------|---------------|------|
| `setup.status` | `{provider_configured: bool}` | [routers/chat.py `_register_setup_handlers`](routers/chat.py) — 复用 connect 时 `resolve_user_llm_config` 加载的 config |
| `setup.runtime_check` | `{ok: bool, error?: str}` | 同上 — `client.models.list()` + `asyncio.wait_for(..., 10s)`；`error` 截断 200 字符防泄漏 key/URL |
| `session.create` | `{session_id, info}` | [routers/chat.py `_register_session_handlers`](routers/chat.py) + [core/ws/runtime_sessions.py](core/ws/runtime_sessions.py) — 开 DB 行，以 `conversation_id` 字符串作为 `session_id` |
| `session.resume` | `{session_id, message_count, messages, info}` | 同上 — 入参 `session_id` 是 DB stored id（int→str）；`core/chat/history.build_session_messages` 共享 tool-name 重建逻辑 |
| `session.title` | `{title}` | PATCH `conv.title` |
| `session.steer` | `{status: "queued"}` | `runtime.steer_queue.put_nowait(text)`；`run_chat_turn` 在 `_run_tool_batch` 之间消费 |
| `session.interrupt` | `{}` | `runtime.chat_task.cancel()`；同时 `dispatcher.push_event("session.info", ...)` 发 terminal 事件让 renderer 解 busy 状态 |
| `session.cwd.set` | `SessionRuntimeInfo` | 更新 `runtime.cwd` + `Conversation.cwd` |
| `session.usage` | `{calls, input, output, total}` | `Message` SUM 子查询（沿用 `routers/sessions.py:55-87` 模式） |
| `session.close` | `{}` | `runtime_sessions.pop`；若有 in-flight chat_task → cancel；renderer 把它当 best-effort cleanup |
| `prompt.submit` | `{queued: bool}` | [routers/chat.py `prompt_submit`](routers/chat.py) — 校验 runtime、阻止并发 turn、可选 `truncate_before_user_ordinal`（删除第 N 条 user 消息起的后续行）；spawn `run_chat_turn` 后台任务，绑定 `runtime.chat_task`；return 后 renderer 等待事件流（`message.start` → `message.delta` × N → `message.complete` + `session.info` heartbeat）。subagent 路径（`agent_delegate_tool` → `HeadlessEmitter`）不受影响，raw 帧照旧 |
| `slash.exec` | `{output?, warning?}` | [core/slash_commands.py](core/slash_commands.py) — renderer 主调用（`use-prompt-actions.ts:651`）；command 字段已去前导 `/`；支持 `/model`、`/yolo`、`/reasoning`；未知命令返回 `warning` 让 renderer fallback 到 `command.dispatch` |
| `command.dispatch` | `{type, ...}` | [routers/chat.py](routers/chat.py) — `slash.exec` 失败时的 fallback（`use-prompt-actions.ts:771`）；先尝试 `slash.exec`，失败返回 `{type:"send"}` |
| `commands.catalog` | `{pairs?, categories?}` | [routers/chat.py](routers/chat.py) → [core/slash_commands.py `commands_catalog()`](core/slash_commands.py) — 列表派生自 `_HANDLERS`，永远只列 dispatch 真正能跑的；desktop 调 `use-prompt-actions.ts:739` 后通过 `filterDesktopCommandsCatalog` 自行过滤 |
| `tool.result` | `{}` | [routers/chat.py](routers/chat.py) — desktop 调 `use-message-stream.ts` 回传 Runner 工具执行结果；`call_id` 匹配 `ipc.resolve_future` |
| `compression.respond` | `{}` | [routers/chat.py](routers/chat.py) — desktop 回复压缩同意请求；参数 `session_id: str` 匹配 `pending_compression_consents` dict 的 str key |
| `tools.sync` | `{count: int}` | [routers/chat.py](routers/chat.py) — desktop 上报本地 Runner schema（per-user 动态工具集），写入 `core/tools_runtime/registry.update_runner_tools`；后续 LLM 调用看到的 Runner 工具立刻刷新。失败抛 `JsonRpcError(INVALID_PARAMS, ...)`，响应体不含 `ok` 字段 |
| `config.get` | `{value}` | [routers/chat.py](routers/chat.py) — `key == "project"` 时返回 `{}`（filesystem-only，desktop 在 `gateway.ts::LOCAL_INTERCEPT` 截获本地处理）；其他 key 读 `UserSetting` |
| `config.set` | `{key, scope, value}` / `{key, session_id, value}` | [routers/chat.py](routers/chat.py) — `scope == "global"` 与 `session_id` **互斥**（同时给 = `INVALID_PARAMS`）。`scope == "global"` 写 `UserSetting` 并同步 in-memory `user_settings` 字典（避免 prompt.submit 读到陈旧值）；allow-list: `yolo_mode` / `reasoning_effort` / `service_tier` / `fast` / `enable_background_review`（MCP server 配置不在此列——存在 runner 主机的 `$DESKAGENT_HOME/config.yaml`，由 Desktop MCP 设置页通过 `deskagent:runner-config:write` 写入）。`session_id` 写 `Conversation.settings_json`（同步更新 `RuntimeSession.settings`）；allow-list: `yolo` / `reasoning` / `fast`，merge 阶段由 `_merge_session_settings` 翻译为全局 key 命名空间（[core/chat/chat_service.py](core/chat/chat_service.py)）。布尔写入序列化到小写 `true`/`false`，结构化值（dict/list）走 `json.dumps` |
| `complete.slash` | `{items: [{text, display, meta}]}` | [routers/chat.py](routers/chat.py) — 复用 [core/slash_commands.py `commands_catalog()`](core/slash_commands.py) 做前缀过滤；返回 shape = 桌面端 `CompletionEntry`（[use-live-completion-adapter.ts](../../desktop/src/app/chat/composer/hooks/use-live-completion-adapter.ts)）。`text == "/"` 不再 IndexError（先 `strip('/')` 再 `split`） |
| `complete.path` | `{items: []}` | [routers/chat.py](routers/chat.py) — filesystem-bound 的 defensive stub。真正实现由 desktop 在 `use-gateway-request.ts::tryLocalIntercept` 本地截获后调 `deskagent:fs:completePath` IPC 走真目录；后端在 Docker 中看不到客户端文件系统，所以保留这个注册只为了在 local-intercept 失败时返回 `{items: []}` 而非 `-32601 METHOD_NOT_FOUND` |
| `image.attach` | `{attached, path, ref_text, size[, name, width, height]}` | [routers/chat.py](routers/chat.py) — 接受本地图片或文件 path，生成 `@image:<path>` 或 `@file:<path>` 引用供 LLM 通过 Runner 工具读取 |
| `image.detach` | `{removed: bool}` | [routers/chat.py](routers/chat.py) — unlink 单个 attachment，路径必须在 attachment root下 |
| `reload.mcp` | runner 返回的 body / `{status: "runner_offline"}` | [routers/chat.py](routers/chat.py) — 通过 [core/ws/ipc.py `dispatch_user_event`](core/ws/ipc.py) 下发 `mcp.reload` 事件到 Desktop；Desktop 经 `deskagent:runner:dispatch` IPC 把该事件原样转发为第一类 JSON-RPC `mcp.reload` 方法（不走 `execute_tool`），等 `tool.result` 回包（60s 超时）。MCP server 列表实际存储在 runner 主机上的 `$DESKAGENT_HOME/config.yaml`（由 Desktop MCP 设置页通过 `deskagent:runner-config:write` 写入），runner 在下次 `mcp_*` 工具调用时惰性重读该 YAML。MCP 连接管理在 Runner（[runner/server.py `mcp.reload` 分支](../runner/server.py) → [runner/tools/mcp/mcp_tool.py `reload_mcp_servers`](../runner/tools/mcp/mcp_tool.py)） |

##### Reserved methods（backend 注册但 renderer 当前未通过 chat WS 触发）

下列方法 backend 已注册且可响应，Desktop 当前不通过 chat WS 调用它们：

| Reserved 方法 | 当前用途 / 状态 |
|---|---|
| `setup.status` | 预连接健康探测入口——renderer 走 `GET /api/status` 拿当前快照，chat WS 不消费 |
| `setup.runtime_check` | 预连接健康探测入口——命中 provider 时 `client.models.list()` + `asyncio.wait_for(..., 10s)`，error 截断 200 字符防泄漏 key/URL |

`session.title` / `session.cwd.set` / `session.usage` / `session.close` / `session.steer` / `session.interrupt` 不属于 reserved——它们在 Desktop chat WS 路径上有显式调用方：[use-prompt-actions.ts:727](desktop/src/app/session/hooks/use-prompt-actions.ts)、[:921](...)、[:947](...)、[use-cwd-actions.ts:80](desktop/src/app/session/hooks/use-cwd-actions.ts)、[use-session-actions.ts:432](desktop/src/app/session/hooks/use-session-actions.ts)。

新增 chat 端功能时如需复用 reserved 方法，要么补 Desktop WS 调用方，要么在 REST / local-intercept 层提供等价能力——不要让 reserved 方法重新变成 desktop 不可见的隐性 backend 行为。

#### `session.info` heartbeat

每个 turn 用 `running: true/false` bracket 包住，保证 renderer 的 `state.busy` 在每个出口都正确清空。turn 期间每 20 秒发一次周期 heartbeat 保持 model/provider 字段新鲜。snapshot 形状见 [core/ws/runtime_sessions.py `runtime_info_snapshot`](core/ws/runtime_sessions.py)。

#### `truncate_before_user_ordinal`

renderer 在 edit / regenerate 提交时传（[desktop/src/app/session/hooks/use-prompt-actions.ts:1009, 1064](../desktop/src/app/session/hooks/use-prompt-actions.ts)）。`prompt.submit` handler 在 spawn turn 前删除第 N 条 user 消息起（含）之后的所有行（user + assistant + tool），保证 `run_chat_turn` 读到的 history 是干净的。N 越界或非 int → `INVALID_PARAMS`（renderer 把这识别为 stale-target 信号，走 `session.resume` 重试）。

### Runtime Session 状态

`/api/chat/ws` 每个连接持有一份 `runtime_sessions: dict[str, RuntimeSession]`，键是 `Conversation.id` 的 str 形式。设计意图：`session.create` 预创建 DB 行并返回 ID，renderer 无需等待首次 turn 即拿到可路由标识；WS 重连后 `session.resume` 重新挂载 in-memory runtime 恢复 cwd/branch 上下文。

已知限制：
- `UserSetting.terminal.cwd` 与 `Conversation.cwd` 并存：前者全局 fallback，后者 per-session 覆盖
- `/yolo` / `/reasoning` slash 命令改全局设置；per-session 切换必须经 `config.set({key, session_id, value})`

## 架构决策

### 工具三层分类

`core/tools_runtime/registry.py` 把所有工具分为三类：

| 类型 | 执行位置 | 工具列表 |
|------|----------|----------|
| **backend tools** | 服务端进程内 | `web_search`、`web_extract`、`cronjob`、`image_generate`、`text_to_speech_tool`、`send_message_tool`、`agent_delegate_tool`、`search_tools` |
| **memory tools** | `NativeMemory.execute_tool` | `memory_retain`、`memory_recall`、`memory_forget` |
| **runner tools** | 通过 IPC 下发给 Runner | 由 Desktop 通过 `tool.call` / `tool.result` 按需调用 |

**LLM 可见性（`CORE_TOOLS` in `core/chat/chat_service.py`）**：上面所有 backend + memory 工具**默认全部**在 chat 起始时直接暴露给 LLM schema（`CORE_TOOLS` 白名单）。`search_tools` 仍保留作为运行时按需解锁额外工具的入口，但日常 chat 不再依赖它去发现上述能力——LLM 直接看到完整 schema。Runner 注入的工具（`read_file` / `write_file` / `patch` / `terminal` / `process` / `browser_*` / `skill_*`）同样在 CORE 中。

**Config-aware 过滤**（`core/tools_runtime/registry.py`）：每个 backend tool 声明 `availability_check(user_settings) -> bool`，`get_all_schemas` 按 check 静默过滤不可用项（拿不到 settings 时回退"全部可见"）。今天只有 `web_extract` 用这条机制——registry 与 dispatcher 共用 provider 解析路径，避免 gate 漂移。Predicate 异常时单 tool 静默隐藏（fail-closed）。

**判定标准**：
- **backend tools**：需要服务端资源（LLM API key、DB、外部 API 凭证）或无副作用的云端 API 调用。`agent_delegate_tool` 自己 spawn 子 agent 跑完整 chat-turn，不走 Runner——因为它需要 Backend 的 LLM 编排能力。`cronjob` 是 Backend 工具因为调度器在 `core/async_jobs/cron.py` 进程内。

**陪伴语义映射**：上述 backend tools 在 DeskAgent 新定位下恰好覆盖伙伴的核心能力——`image_generate` 生成专属桌面形象、`text_to_speech_tool` 给伙伴语音、`send_message_tool` 让伙伴主动发起对话、`web_search`/`web_extract` 让伙伴帮用户查信息。完整映射见 [design.md §7.4](../design.md#74-已有云端工具在新定位下的复用映射)。
- **memory tools**：独立第三类——注入 DB session 走 `NativeMemory.execute_tool`，既不需要本地环境也不调外部 API，但需要 Backend 的 DB 访问。
- **runner tools**：需要用户本地环境（终端、文件系统、浏览器、代码执行）或用户设备上的操作。Backend 无法直接执行这些操作，必须通过 IPC 下发。

### IPC Future 桥接

`core/ws/ipc.py` 维护 `_pending: dict[(user_id, call_id), Future]`——键是 `(user_id, call_id)` 而非单 `call_id`(并发用户不共享 future;`user_id` 来自 JWT 解析;WS 断开时 `discard_user` 取消该用户所有未决 future):

1. 调用 Runner 工具时 `create_future`，发 `tool.call` JSON-RPC 事件到 Desktop
2. `await fut` 阻塞等待
3. Desktop 回传 `tool.result` JSON-RPC 请求后 `resolve_future` 唤醒
4. 超时 `settings.ipc_future_timeout_seconds`（默认 300s，IPC future 等待 Runner 工具结果），超时返回 synthetic error
5. `compression.request` 独立超时 = 300s（`Settings.compression_consent_timeout_seconds`），超时发 `compression.timeout` 事件
6. 通用 `dispatch_user_event(user_id, event_type, payload, *, dispatcher, timeout)` —— `reload.mcp` 用同一 future 通道发任意 JSON-RPC 事件，desktop 收到后转发给 Runner 并把响应经 `tool.result` 路径回包（key 用 outbound payload 里注入的 `call_id`）

#### 快速失败

`_dispatch_runner_tool` (`core/chat/chat_service.py`) 的 `try/except (WebSocketDisconnect, RuntimeError)` 包裹 `emitter.send_json`——`WSEmitter` 吞掉了这两类异常,但 chat loop 必须看到快速失败才能避免 `await_future` 悬挂 300s。Desktop 侧的协议级契约见 [desktop/README.md §工具调用快速失败](../desktop/README.md)。

### 工具调用消息流（一次 turn）

`core/chat/chat_service.py:run_chat_turn`：流式调 LLM → ThinkScrubber 清洗 → 累积 tool_calls → 修复坏 JSON + 类型强转 → guardrails 判定 → 按 location 分发 → redact → 写 DB。`MAX_AGENT_LOOP_TURNS = 15` 硬上限。turn 结束后异步触发 `background_review` 提取长期记忆。

### 消息清理与截断

`core/chat/message_sanitization.truncate_chat_history`：保留最近 40 条非 system 消息；旧于最近 10 条做 image normalize（`[screenshot]`）；单条字符上限 15000。

### Cron 调度

`core/async_jobs/cron.scheduler_loop` 每 60s `_tick()`：扫描到期任务，CAS 推进 `next_run_at`（多副本安全），写 `cron.trigger` 到 `ws_events` outbox。无效 cron 表达式自动暂停 job。

跨副本：每个 replica 独立 CAS 推进，`cron.trigger` 走 outbox 防重复投递。`_tick` 不 await WS 推送，慢 WS 不卡 cron 事务。

### 系统提示词组装

`core/chat/system_prompt.build_system_prompt_parts`：stable / context / volatile 三段。按模型族注入执行纪律（OpenAI/Gemini/Grok）。Steer 通道用 `[OUT-OF-BAND USER MESSAGE]` 标记。

### 错误处理策略

`core/llm/error_classifier.py` 实现结构化错误分类，`classify_api_error()` 返回结构化分类结果(见 `core/llm/error_classifier.py::ClassifiedError`)——根 `CLAUDE.md` 文档分层规则禁止在 CLAUDE.md 复读字段名。

**分类法**（`FailoverReason` 枚举，21 种）：

| 类别 | reason | 恢复策略 |
|------|--------|----------|
| 认证 | `auth` | 刷新/轮换凭证 |
| 计费 | `billing` | 立即轮换凭证 |
| 限流 | `rate_limit` | 退避后轮换 |
| 服务端 | `overloaded` / `server_error` | 退避重试 |
| 传输 | `timeout` | 重建客户端 + 重试 |
| 上下文 | `context_overflow` | 压缩上下文，不 failover |
| 负载 | `payload_too_large` / `image_too_large` | 压缩后重试 |
| 模型 | `model_not_found` | fallback 到其他模型 |
| 内容策略 | `content_policy_blocked` / `provider_policy_blocked` | 不重试相同内容 |
| 格式 | `format_error` / `invalid_encrypted_content` | strip + 重试 |
| 附件 fetch | `attachment_fetch_failed` | 不重试——LLM 无法下载临时媒体文件（例如链接过期或网络隔离），回包给 renderer 时由 [core/chat/chat_service.py `_llm_error_user_message`](core/chat/chat_service.py) 翻成 provider-agnostic 短消息，避免 raw 报错输出，提示用户尝试重新上传。 |

**分类流水线**（8 步优先级）：provider patterns → HTTP status → error code → message pattern → SSL/TLS transient → server disconnect → transport heuristics → unknown fallback。`_ATTACHMENT_FETCH_PATTERNS` 在 billing 之后、generic 400 之前匹配，防止退化为 `format_error`。

**REST 错误信封**：`/api/llm/completion` 与 `/api/media/*` 在异常路径上调用 `classify_api_error`，把 `FailoverReason` + `status_code` 折成 `{error, reason, status}` 返回给 renderer。原始异常（可能带 provider URL / 部分 auth header）只写服务端 log，**永远不出后端**——满足 `design.md §9` 的 -32603 "no internal detail" 契约。

### Observability

- **日志**：`backend/logger.py` 集中入口（`get_logger(__name__)`）+ `setup_logging()` lifespan 接管 root logger；`Settings.log_level` / `log_format` 部署可调（dev: text / prod: json）；stdout only，轮转由 Docker 外部 driver 负责。**脱敏责任在调用方**——`core.chat.chat_service` 等 LLM 路径在打 log 前已跑 `redact_sensitive_text`，logger 是基础设施层**不** import `core.*`（避免循环依赖）。`extra` dict key **禁止**与 stdlib `LogRecord` 内置属性同名（命中 = `KeyError` 崩溃），完整保留字表见 [logger.py `_RESERVED_LOGRECORD_KEYS`](logger.py)。
- **correlation ID**：每个 HTTP 请求经 `core.correlation.correlation_id_middleware` 解 `X-Request-ID` header（缺则生成）写入 ContextVar；WS path 在 [routers/chat.py](routers/chat.py) 直接读 header，**不依赖** middleware。跨 `asyncio.create_task` 自动透传（CPython 3.11+ Task 携带 context），**不要** wrap。长生命周期 task（`cron.scheduler_loop`、`connection_manager._process_events`）每个 tick 顶部注入 tick-scoped ID。
- **metrics**：无 `/metrics` 端点
- **tracing**：无 OpenTelemetry 集成

### 中间件模板

项目里所有 `app.middleware("http")` 注册的中间件遵循同一 shape：`async def mw(request, call_next): 读 header/state → 调内部 setter → await call_next → (可选) 写 response header`。实例见 [`core/rate_limit.py stash_user_id_middleware`](core/rate_limit.py) 和 [`core/correlation.py correlation_id_middleware`](core/correlation.py)。注册顺序在 [main.py](main.py) — 后注册为 Starlette 外层 wrapper；带状态注入的中间件应放内层（`stash_user_id`），跨域 ID 注入放外层（`correlation_id`）。**不要**抽 base class——两个 middleware 的失败回退逻辑差太多，抽 base 比直接两份还复杂。

### 限流与防滥用

[slowapi](https://github.com/laurentS/slowapi)（in-memory moving window）通过 [core/rate_limit.py](core/rate_limit.py) 集中实现。阈值在 `Settings` 字段暴露，运营侧可通过 `.env` 调整无需改码；master switch `rate_limit_enabled` 设 False 切到 no-op 限流器。5 个受保护端点：`login`（per-IP）、`llm/completion`（per-user + per-IP 双层）、`stt`/`tts`/`image_gen`（per-user）。

**per-user key 设计权衡**：middleware 仅做 JWT 签名校验 + 解析 `sub` → stash `user_id`（不做 DB 查），handler 的 `Depends(get_current_session)` 做完整校验。伪造 token 要么签名失败（降级 per-IP）要么通过验证（拿到合法 user 的桶），无安全放大风险。429 响应遵循 `{error, reason, status}` + `Retry-After: 60`。

**多副本约束**：in-memory storage 意味着 N 副本 = N× 单副本有效配额，未来迁 Redis 是单点改动。**反向 RPC 端守卫**（[desktop/electron/runner-reverse-rpc.cjs](../desktop/electron/runner-reverse-rpc.cjs)）是单次大小/数量维度的保护，与 backend 速率限流不替代。**fail-open**：slowapi 内部异常不阻断请求（默认放行）。

### Feature Flags

`config.py` 的 `Settings`（pydantic-settings，env / .env，需重启）+ DB `UserSetting` 表（可热改）两层。LLM 调用韧性参数（timeout / retry / delay）全部 deployment 级。

### IPC Future JWT 过期处理

如果 token 在 IPC 飞行途中过期（见上文 [IPC Future 桥接](#ipc-future-桥接) 的键定义）：Desktop 的 `tool.result` 被 `auth_helpers` 拒绝 → IPC future 永久挂起直到 300s 超时 → 返回 synthetic "runner offline" error。

**已知限制**：多个并行 IPC 时 token 失效会导致所有 future 同时挂起 300s。`discard_user` 在 WS 断开时清理，但 token 过期不触发 WS 断开。当前靠超时兜底，不视为待修缺口。

## 安全设计

### Tool Reserved Keys 防注入

`registry.execute_backend_tool` 把 `user_id`、`llm_config`、`user_settings` 标记为 reserved——LLM 塞同名 key 静默丢弃。

### 不可信工具结果包裹

`web_search`/`web_extract`/`browser_*`/`mcp_*` 的字符串结果用 `<untrusted_tool_result>` 包裹。短字符串（`untrusted_wrap_min_chars`，默认 32）不包——注入风险低 + 节省 token。

### DNS Rebinding 防护

`send_message_tool` 出站前 `getaddrinfo` 校验目标 IP，拒绝 loopback/private/multicast。

### Think Block 清洗

`core/chat/think_scrubber.StreamingThinkScrubber` 流式过滤 `<think>`/`<thinking>`/`<reasoning>` 标签。

### Secret Redaction

`core/redact.redact_sensitive_text`：36 prefix patterns + regex rules。import 时快照 `DESKAGENT_REDACT_SECRETS`（anti-tamper）。

### API Key Fingerprinting

`GET /api/user/model-config` 返回的 `llm_api_key` 用 `utils.text.fingerprint_api_key` 派生一个稳定但不可逆的展示标签（如 `sk-…89`），原始 key 永不离开后端——LLM 调用走服务端路径读 DB（`core/chat/chat_service.resolve_user_llm_config`）。

## 数据库

- **引擎**：PostgreSQL（`postgresql+psycopg://`），连接池 `pool_size=20, max_overflow=10, pool_recycle=3600, pool_pre_ping=True`
- **Schema**：`Base.metadata.create_all` + `_install_ws_notify_trigger`（PG trigger 需手动 DDL；无 Alembic）。新增列通过 [main.py `_install_schema_extensions`](main.py) 走 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`（PG 9.6+ 幂等），避免破坏已部署实例
- **WS 事件通知**：`ws_events` 表上的 PG Trigger 在 INSERT 时 `NOTIFY ws_events_channel`。`core/ws/connection_manager.ws_event_loop` 通过 `asyncpg` 独立连接 `LISTEN`，`DELETE ... RETURNING` 原子认领 + 派发（多副本不重复投递）。60s 超时兜底 GC。

### Per-Session 配置

`Conversation.settings_json` 存储 per-session 覆盖（renderer 别名 `yolo` / `reasoning` / `fast`）。`config.set` 同时写 in-memory runtime 和 DB；`session.resume` 重新水合；WS 重连不丢失。`_merge_session_settings` 把 per-session overrides 翻译为全局 key 命名空间（`yolo` → `yolo_mode`），下游消费方从此单一注入点读取有效值。

### 附件引用（Path-Mode）

[core/attachments.py](core/attachments.py) 提供路径引用注册和安全删除。`image.attach` 生成 `@image:<path>` 引用，LLM 通过 Runner 文件工具直接读取本地文件，backend 无需暂存。`DELETE /api/sessions/{id}` 触发 `gc_session` 级联删除（双层防御：session_id 正则 + `is_relative_to` 校验）。

### 伙伴人格与形象系统（设计文档 §7）

`Persona` 表存用户 onboarding 产出的角色定义（结构化 JSON + 渲染好的 `system_prompt_extras` 片段），注入每次 chat turn 的 system prompt；`AvatarAsset` 表存历次生成的专属形象资产，由 Postgres partial unique index 约束"每个用户最多一条 active 记录"。`/api/companion/*` 五个路由覆盖 onboarding 流程：get/put persona、extras、get 激活形象、avatar history、generate new avatar。

形象生成失败时返回 `502` + 友好文案（`伙伴形象生成失败，请稍后重试`），不泄露 provider 原始错误——参考 [design.md §9.2](../design.md) 的错误收拢原则。

## 已知限制

| 限制 | 说明 |
|------|------|
| Runner tools 在 desktop 离线时延迟返回 | ipc.await_future 有 300s 超时；`_dispatch_runner_tool` 做三层 fast-fail（active_connections → has_runner_tools → send_json 异常），通常 < 100ms 返回离线错误，仅绕过三层后才进入 300s 超时 |
| 并行 terminal 不可用 | Runner 端共享 LocalEnvironment 实例，快照文件不可并发写。架构决定，不视为待修缺口 |
| `apply_partial` 抹除"清空"语义 | PATCH 无法用 null 清字段 |
| 形象资产 URL 有 TTL | `AvatarAsset.asset_url` 直接保存 provider 返回的 URL；Desktop 必须在收到时立刻本地缓存，未来过期后无法直接重读需重新生成 |
| `image_generate` / `text_to_speech_tool` 不参与 config-aware 过滤 | 可用性取决于 LLM provider，需调用时拿 `llm_config`；Registry 构造时无从判断。LLM 维度门控是未来的工作 |

## Web Providers

所有 provider 实现 `core/tools_runtime/web_providers/__init__.py` 的 `WebSearchProvider` ABC，dispatcher 通过 `user_settings` 的 `web.backend` / `web.extract_backend` 选 provider。凭证**按用户注入**（先读 `user_settings`，否则回落 `os.getenv`），避免多用户共用 env-var key。

三个 provider：**ddgs**（搜索默认，无需 key）、**brave-free**（纯搜索，免费 2000 次/月）、**tavily**（搜索+提取，`web_extract` 默认 backend，唯一实现 `extract()`）。

| 工具 | 默认 | 缺 key 时 |
|------|------|-----------|
| `web_search` | `ddgs` | 静默回退到 `ddgs`（搜索始终能用） |
| `web_extract` | `tavily` | 返回明确错误，**不回退** |

未知 backend 值：search 走 `ddgs`、extract 走 `tavily`，打 `ERROR` 日志。

`web.brave_api_key` / `web.tavily_api_key` 在 `GET /api/config` 响应里**不返回原始值**，替换为 `*_set: bool` + `*_fingerprint: str`。`PUT /api/config` 路径丢弃计算字段避免数据污染。

## 伙伴人格与形象系统（新定位核心）

DeskAgent 从工具型 Agent 重新定位为定制化陪伴型桌面伙伴后，Backend 在现有架构上新增两项跨模块契约。设计意图详见 [design.md §2 伙伴生命周期](../design.md) 与 [§7 伙伴形象与角色系统](../design.md)。

### 角色定义（Persona）

onboarding 产出的结构化角色定义（名字、性格、说话风格、外貌/风格偏好等），按用户维度持久化，作为系统提示词的一部分注入该用户每次对话（由 `core/chat/system_prompt.py:build_system_prompt_parts` 装配）。角色定义是伙伴行为的**唯一真相源**，只能由用户显式发起变更，禁止 LLM 自行改写——与现有 reserved 键防注入机制（`registry.execute_backend_tool` 拦截 `user_id`/`llm_config`/`user_settings`）同属一道防线。

### 形象资产（Avatar Asset）

Backend 据角色定义装配生图 prompt，调用现有 `backend_tools/image_generation_tool.py` 产出专属形象资产，与角色定义一同按用户维度持久化，经 `core/ws/ipc.py:dispatch_user_event` 下发至 Desktop 渲染。形象在多次会话间保持一致；变更走受控再生成流程（用户主动触发或未来扩展的成长机制）。

### 复用映射（现有能力 → 伙伴场景）

| 能力 | 现状 | 伙伴场景复用 |
|------|------|-------------|
| 长期记忆 | `Memory` 表 + `tools_runtime/memory.py` | 直接复用——伙伴对用户的长期记忆 |
| 主动陪伴调度 | `CronJob` 表 + `core/async_jobs/cron.py` | 直接复用——伙伴定时问候/提醒 |
| 事件下发 | `WSEvent` + LISTEN/NOTIFY | 直接复用——伙伴主动消息送达 Desktop |
| 生图凭证 | `UserModelConfig.image_gen_*` 字段 | 直接复用——形象生成的 provider 配置已就位 |
| 生图执行 | `backend_tools/image_generation_tool.py` | 直接复用——产出专属形象资产 |
| 伙伴语音 | `backend_tools/tts_tool.py` | 直接复用——让伙伴"能说" |
| 主动消息 | `backend_tools/send_message_tool.py` | 直接复用——让伙伴主动发起对话 |

## 重构行动项

Backend 整体保留复用——三模块解耦、JSON-RPC、工具调用流、反向 RPC、Outbox/Cron、安全边界、错误契约均不变。新定位下的增量工作如下。

### 新增
- **角色定义数据模型**：在 `models.py` 新增 `Persona` 表（用户维度、一对一、结构化角色定义 JSON）。配套 REST 或 WS 端点供 Desktop onboarding 读写。
- **形象资产数据模型**：在 `models.py` 新增 `AvatarAsset` 表（用户维度、形象资产引用与元数据、生成状态），配套下发端点。表结构走 [main.py `_install_schema_extensions`](main.py) 的 `ADD COLUMN IF NOT EXISTS` 幂等路径，与无 Alembic 的现状一致。
- **角色定义注入**：在 `core/chat/system_prompt.py:build_system_prompt_parts` 的 stable 段装配角色定义，驱动伙伴说话风格与性格。
- **形象生成编排**：onboarding 用户确认后，Backend 装配 prompt → 调 `image_generate` → 写 `AvatarAsset` → 经 `dispatch_user_event` 下发"孵化"事件到 Desktop。
- **形象生成失败友好回包**：在 `error_classifier` 或 `chat_service` 层为形象生成路径提供可理解的失败提示与重试（design.md §9.2），不透传生图服务原始报错。

### 改造
- **onboarding JSON-RPC 方法**：在 `routers/chat.py` 注册表新增 `persona.create` / `avatar.generate` / `avatar.status` 等方法，复用现有 IPC future 与事件通道，不引入新链路。

### 保留（不动）
全部现有工具三层注册表、IPC future 桥接、Cron 调度、错误分类管道（21 种 `FailoverReason`）、安全防护网（reserved 键 / untrusted wrap / Tirith / SSRF）、数据库 LISTEN/NOTIFY 触发器、Web Providers、限流、自更新端点。
