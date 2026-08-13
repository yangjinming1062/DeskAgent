# Backend

云端大脑——FastAPI + PostgreSQL + JWT。承载 DeskAgent 伙伴的"人格"（角色定义 + 长期记忆）与"形象"（专属形象资产生成与下发），负责 LLM 流式对话编排、系统提示词装配、云端工具执行、Cron 调度，以及通过 IPC 将本地工具调用下发给 Client/Runner。

## 1. 职责与边界

**职责**：伙伴角色定义与形象资产生成与下发、LLM 流式对话编排、系统提示词装配、云端工具执行、Cron 调度、跨模块事件下发（WS outbox）、REST + WebSocket 端点暴露。

**不**做：
- **不接触用户本机操作系统**——所有本机操作经 IPC 委托给 Runner；图像/视频/语音等资产仅在云端生成、Client 拉取后渲染。
- **不持有终端 / 浏览器会话**——这些都在 Runner 进程内。
- **不渲染桌面伙伴**——形象与动画完全是 Client 责任（[DESIGN.md §1](../DESIGN.md)）。
- **不做 LLM provider 特定的 schema 适配**——nullable union 原样传给 OpenAI-compatible provider，由 provider 决定是否接受。

架构层定位见 [ARCHITECTURE.md §1 / §2](../ARCHITECTURE.md)；跨模块契约见 [PROTOCOL.md](../PROTOCOL.md)；错误分层见 [ARCHITECTURE.md §8](../ARCHITECTURE.md)。

## 2. 设计意图

- **持久化伙伴身份与资产**：角色定义、形象资产、长期记忆都是**跨设备、跨会话**的核心资产，必须云端托管且按用户维度隔离。
- **LLM 流式编排作为唯一对话入口**：所有用户消息经 backend chat service 走完整 turn（角色定义装配 → 上下文管理 → LLM 调度 → 工具路由 → 响应流式下发）；不绕过、不分支。
- **数据库 schema 无 Alembic**：以 `ModelBase.metadata.create_all` + PG `ADD COLUMN IF NOT EXISTS` 幂等迁移；新增列安全，已部署实例不破坏（[已知限制](#6-已知限制)）。
- **Provider 自注册 + 三层入口**：每个 provider 模块在 module bottom 调 `REGISTRY.register`，`main.py` 显式 import 触发；chain resolver 不从 `base_url` host 反推（避免脆弱推断）。三层入口（`provider_for_service` / `client_for_service` / `execute_with_fallback`）按场景路由。
- **Outbox + LISTEN/NOTIFY 取代轮询**：伙伴主动消息、Cron 任务、形象/角色变更通知全经 PostgreSQL 触发器 + `ws_events` 表，毫秒级、可水平扩展。
- **形象资产生成受 rate-limit + per-user 锁守护**：生成是同步、阻塞 UI 的高成本路径，不并发、不公开 provider 原始错误。

## 3. 架构地图

```
backend/
├── common/ · components/    # 框架基座（基类 + 有状态基础设施单例 + 横切层 correlation/redact/attachments/temp_files）
├── modules/                  # 按 domain 分包的 ORM 模型 + Pydantic 契约
├── services/                 # 业务/编排层，无 facade，按子包直接 import
│   ├── auth/                 # 身份凭据能力集与配置响应构建
│   ├── chat/                 # 对话编排（含 chat_emitter 收敛 ↔gateway import 环）
│   ├── companion/            # 角色定义、形象资产、affect、voice catalog、wardrobe
│   ├── gateway/              # JSON-RPC + WS 入口 + IPC future
│   ├── llm/providers/        # Chat/ImageGen/VideoGen/TTS/STT 五类 provider ABC
│   ├── scheduler/            # Cron + 主动消息调度 + 夜间自主活动批处理
│   ├── tools/                # 工具层（backend / memory / runner 三类）
│   ├── update/               # 桌面客户端版本更新清单构建
│   └── desktop_config.py     # 桌面配置默认值与铺平转换
└── api/v1/ + main.py         # 薄 HTTP/WS 端点（pkgutil 自动发现）+ lifespan + 路由装配
```

依赖方向（低 → 高）：`common` / `components`（框架基座，**不** import modules/services）→ `modules`（领域模型与契约）→ `api/v1`（端点）/ `services`（服务层）→ `main.py`，无反向。

**循环断路器**：`chat ↔ gateway`、`chat ↔ scheduler`、`chat → companion → tools.builtin → scheduler → chat` 三条 import 环全部经 `services/chat/chat_emitter.py`（`Emitter` 协议，零内部依赖）收敛；重编排器/`turn_inputs`/`agent_delegate` 经 `__getattr__` 懒加载，保证 `import services.chat` 不会触发整张服务图。

## 4. 关键设计决策

- **夜间自主活动批处理（Stage 0–4 独立事务）**：在用户本地休息窗口（0:00–5:00）调用 LLM 运行批处理流水线（画像推断、记忆整理与衰退、主动规划、自我日记）。每个阶段独立事务并具备全空回滚安全网。**为什么不作为在线 agentic 循环**：批处理在离线状态也可完成用户认知深化与记忆优化，不强依赖客户端长连接；次日生成的 CronJob 在触发派发时才执行在线与 disturbance 检查。
- **TOML 模版与 Git 隔离配置管理**：Git 统一托管默认配置模版 `config.toml.example`，`components/config.py` 中不保留重复默认值与冗余注释。开发者本地配置写在 `config.toml`（已被 `.gitignore` 忽略）。系统按 `OS Env > .env > config.toml > config.toml.example` 优先级加载。
- **provider 自注册而非手动 `main.py` 引入**：`services/llm/providers/<name>/__init__.py` import 时注册到 `_REGISTRY`；新增 provider 子包即可扩展能力，无须修改 `main.py`。**代价**：注册顺序敏感——`main.py` 显式 import 触发；遗漏某 provider 则该能力静默缺失（fail-open）。
- **IPC future 按 `(user_id, call_id)` 二元键寻址**：并发用户不共享 future；`discard_user` 在 WS 断开时取消该用户所有未决 future。**为什么不只 `call_id`**：跨用户 key 复用会泄露上下文。
- **Outbox + LISTEN/NOTIFY 而非直推 WS**：调度器 tick 只写入事件不 await WS 发射，慢客户端不拖垮事务；副本原子认领保证单发。**为什么不直接推 WS**：WS 不可水平扩展，且无法处理后端 OOM/重启时的未发事件。
- **WS 关闭码 1008（鉴权失效）立即退出重连流程**：避免在 token 失效状态下重试 N 次把请求堆到过期账号上。**为什么不让 retry**：用户凭据问题靠重试无法恢复。
- **形象生成失败对用户返回 502 + 固定文案**：作为陪伴场景的关键路径，需向用户返回可理解的友好提示并支持重试，不暴露生图服务原始错误。**为什么不透传 provider 错误**：provider 错误体常含 URL / 部分 auth header，且用户对生图服务错误无处理能力。
- **API Key 永不离开后端（fingerprinting）**：`GET /api/user/model-config` 只返回 `sk-…XX` 形式的指纹 + `_set` 布尔。**为什么不分两条**：用户自助配置时不需要原始 key 重新输入；admin 端点单独走 `PUT /api/admin/{user_id}/model-config` 强制三字段非空。
- **错误分类管道收敛为 21 种 `FailoverReason`**：8 步优先级过滤决定恢复策略（退避重试 / 凭证轮换 / 压缩上下文 / 不重试）。**为什么不暴露原始异常**：provider 错误常含 URL / 部分 auth header / 私有 SDK 调用栈，必须脱敏。
- **3D 模型生成管线双轨制（Tripo3D 主路 + Blender+LLM 回退）**：默认 Tripo3D 高保真度生成；当 `tripo_api_key` 缺失 / 余额为 0 / Tripo API 返回 credits 耗尽错误模式时自动回退到 Blender+LLM 管线，或由 `ModelGenerateRequest.provider` 显式锁向。**为什么不只用一条**：Tripo 商业 API 有成本与可用性限制（积分、断供、地区封锁）；自由形式 LLM 写 bpy 代码是 last-resort 兜底，质量显著低（无 PBR 纹理）但成本仅为 LLM tokens + 本地 CPU Blender render。Blender 子进程与 Backend 同用户运行——LLM 写代码本身就把 LLM 当作可执行代码生成器，威胁向量与现有 LLM 调用同等级，详见 [ARCHITECTURE.md §10](../ARCHITECTURE.md) 安全层不变量。

## 5. 与外部的契约

| 契约 | 方向 | 在哪定义 |
|------|------|---------|
| 伙伴层 JSON-RPC 方法（onboarding / avatar / companion / model / tts） | 对 Client | [PROTOCOL.md §1.1](../PROTOCOL.md) |
| 事件类型（`companion.affect` / `model.ready` / `wardrobe.updated` 等） | 对 Client | [PROTOCOL.md §1.2](../PROTOCOL.md) |
| Affect emotion / locale 枚举 | 对 LLM 提示词 → Client 渲染 | [PROTOCOL.md §1.3](../PROTOCOL.md) |
| 资产 URL 5 分钟 HMAC 签名 | 对 Client | [PROTOCOL.md §1.4](../PROTOCOL.md) |
| 错误信封 `{error, reason, status}` + JSON-RPC 错误码脱敏 | 对 Client | [PROTOCOL.md §1.5](../PROTOCOL.md) |
| Reserved Keys 防注入（`user_id` / `llm_config` / `user_settings`） | 对 LLM 工具入参 | [PROTOCOL.md §5.1](../PROTOCOL.md) |
| Outbox `ws_events` LISTEN/NOTIFY 调度 | 内部（业务 / Cron → Client） | [ARCHITECTURE.md §5](../ARCHITECTURE.md) |
| IPC future `(user_id, call_id)` 键语义 + 超时 + JWT 过期兜底 | 内部（Backend ↔ Client IPC） | [PROTOCOL.md §4](../PROTOCOL.md) |
| disturbance_tier 镜像（`_disturbance` 字典） | 接 Client 推送 [PROTOCOL.md §1.1](../PROTOCOL.md) 的 `companion.set_disturbance_tier` | [ARCHITECTURE.md §5](../ARCHITECTURE.md) |
| LLM provider chain resolver 三层入口（`provider_for_service` / `client_for_service` / `execute_with_fallback`） | 本模块独有 | 本 README §3 |
| PROVIDER-first 配置 + Tier 1–4 回落链 | 本模块独有（Provider 自注册产物） | 本 README §2 |
| 工具三层分类（backend / memory / runner） | 本模块独有 | 本 README §2 + backend 代码 |
| `ModelGenerateRequest.provider` 取值 + 触发条件 | 对 Client | [PROTOCOL.md §1.3](../PROTOCOL.md) |
| `model.ready` / `model.gen.progress` payload `provider` 字段 | 对 Client | [PROTOCOL.md §1.3](../PROTOCOL.md) |

## 6. 已知限制

| 限制 | 说明 |
|------|------|
| **单实例部署** | `disturbance_tier` 与 IPC future 锁在 process-local 内存；in-memory rate limit（slowapi）；架构不支持多实例水平扩展 |
| **数据库无 Alembic** | 基于 SQLAlchemy `ModelBase.metadata.create_all` 初始化库表与索引，未正式部署前不维护迁移脚本 |
| **`image_generate` / `text_to_speech_tool` / `video_generate` 不参与 config-aware 过滤** | 可用性取决于 provider；调用时按 `llm_config` 拉 `provider_for_service`；缺 key 会走 fallback chain |
| **形象资产 URL 有 TTL** | 持久路径 `companion-avatars/<id>.<ext>` + 读时 5 分钟 HMAC 签名；`verify_signed_asset_request` 强制校验，缺签名 401。Client 必须走 `deskagent:api:asset` 代理而非直连 |
| **MiniMax 视频 URL 短时效** | video_gen v2（H3）`poll` 直接返回 `download_url`，v1（Hailuo）还有 `files/retrieve` 第二跳；两者 URL 都是短时效的，必须**立即下载落 `data_dir/temp-media`**，不能直接返给前端 |
| **MiniMax 内容风控 1027 不重试** | `base_resp.status_code=1027` 映射到 `content_policy_blocked` 且 `retryable=False`，避免重试三次白烧配额 |
| **流式 chat 一旦首 chunk 已发不再 fallback** | 用户已看到部分输出，切换 provider 会造成 transcript 截断；失败统一 raise，由 HTTP envelope 走 `{error, reason, status}` |
| **Cron kick 守卫** | `_kick_autonomous_turn` 仅在 dispatcher 表里查"用户在线"；`kick` 内部 `is_quiet` 守卫 + per-user `asyncio.Lock` 兜底 |
| **附件 fetch 失败独立报错** | LLM 下载临时媒体失败（链接过期、网络隔离）拦截 Proxy 端原始 SDK 报错，返回 provider-agnostic 短消息，避免误导性触发 LLM 回退 |
| **WS 鉴权失效（1008）立即退出重连** | 不在过期 token 状态下重试；用户重新激活后才恢复 |
| **Obs 缺口** | 无 `/metrics` 端点、无 OpenTelemetry 集成；日志 stdout only，dev text / prod json |
| **Blender+LLM 回退管线最坏时长** | 默认 10 轮迭代 × 单次 600s Blender timeout = ~100 分钟一次生成；适合夜间离线场景，不阻塞交互 UI。`blender_llm_max_iterations` / `blender_llm_timeout` 可调 |
| **Blender+LLM 模型质量** | 无 PBR 纹理（仅纯色 Principled BSDF 材质）、几何为 LLM 自由形式生成——视觉保真度显著低于 Tripo3D。LLM 在迭代内可比 preview vs 种子图 → 持续精修 |
