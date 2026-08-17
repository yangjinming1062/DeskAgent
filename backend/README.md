# Backend

云端大脑——FastAPI + PostgreSQL + JWT。承载 SpiritAgent 伙伴的"人格"（角色定义 + 长期记忆）与"形象"（专属形象资产生成与下发），负责 LLM 流式对话编排、系统提示词装配、云端工具执行、Cron 调度，以及通过 IPC 将本地工具调用下发给 Client/Runner。

## 1. 职责与边界

**职责**：伙伴角色定义与形象资产生成与下发、LLM 流式对话编排、系统提示词装配、云端工具执行、Cron 调度、跨模块事件下发（WS outbox）、REST + WebSocket 端点暴露。3D 模型生成（图生3D provider：tripo / hunyuan）与分钟级 Blender 系后处理（morph 注入 / garment 换装）全部入队 `render_jobs`，由**同镜像的 Render Worker 副本**（compose `worker` 服务）认领执行，每轮 Blender 跑在一次性沙箱容器里。

**不**做：
- **不接触用户本机操作系统**——所有本机操作经 IPC 委托给 Runner；图像/视频/语音等资产仅在云端生成、Client 拉取后渲染。
- **不持有终端 / 浏览器会话**——这些都在 Runner 进程内。
- **不渲染桌面伙伴**——形象与动画完全是 Client 责任（[DESIGN.md §1](../DESIGN.md)）。
- **不做 LLM provider 特定的 schema 适配**——nullable union 原样传给 OpenAI-compatible provider，由 provider 决定是否接受。
- **web 进程不执行 Blender 子进程**——3D 模型生成（含 provider 长轮询与 morph 注入）与换装几何管线全部经 render_jobs 转交 worker；web 内只保留 avatar 2D 与 video 生成的既有 job 模式。

架构层定位见 [ARCHITECTURE.md §1 / §2](../ARCHITECTURE.md)；跨模块契约见 [PROTOCOL.md](../PROTOCOL.md)；错误分层见 [PROTOCOL.md §1.6](../PROTOCOL.md)。

## 2. 设计意图

- **持久化伙伴身份与资产**：角色定义、形象资产、长期记忆都是**跨设备、跨会话**的核心资产，必须云端托管且按用户维度隔离。
- **LLM 流式编排作为唯一对话入口**：所有用户消息经 backend chat service 走完整 turn（角色定义装配 → 上下文管理 → LLM 调度 → 工具路由 → 响应流式下发）；不绕过、不分支。
- **数据库 schema 由 Alembic 版本化迁移管理**：lifespan 启动时自动 `upgrade head`（决策细节见 §4）；schema 演进可审查、可回滚，消除 create_all + 手写幂等 DDL 时代"只能加列、不可收紧不可回滚"的盲区。
- **Provider 自注册 + 三层入口**：每个 provider 模块在 module bottom 调 `REGISTRY.register`，`main.py` 显式 import 触发；chain resolver 不从 `base_url` host 反推（避免脆弱推断）。三层入口（`provider_for_service` / `client_for_service` / `execute_with_fallback`）按场景路由。
- **Outbox + LISTEN/NOTIFY 取代轮询**：伙伴主动消息、Cron 任务、形象/角色变更通知全经 PostgreSQL 触发器 + `ws_events` 表，毫秒级、可水平扩展。
- **重生成任务与 web 进程隔离（PG 队列，零 Redis）**：Blender 系分钟级生成入队 `render_jobs`（INSERT 触发 NOTIFY 唤醒 worker 的 LISTEN 专线；认领 = `FOR UPDATE SKIP LOCKED` + CAS UPDATE），web 请求路径毫秒级返回；attempts 封顶 + stale 回收 + 启动清扫覆盖崩溃恢复，语义按 VideoGenJob 先例自管。跨模块拓扑与安全边界见 [ARCHITECTURE.md §2 云端渲染拓扑](../ARCHITECTURE.md) 与 §8 不变量 11。
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
│   ├── conversation/         # 主对话与 subtype 语义的唯一定义处（叶子包，只依赖 modules）
│   ├── gateway/              # JSON-RPC + WS 入口 + IPC future
│   ├── llm/providers/        # Chat/ImageGen/VideoGen/TTS/STT/ModelGen 六类 provider ABC
│   ├── scheduler/            # Cron + 主动消息调度 + 夜间自主活动批处理
│   ├── tools/                # 工具层（backend / memory / runner 三类）
│   ├── update/               # 桌面客户端版本更新清单构建
│   ├── worker/               # render_jobs 认领循环 + Blender 沙箱执行器（独立进程入口）
│   └── desktop_config.py     # 桌面配置默认值与铺平转换
└── api/v1/ + main.py         # 薄 HTTP/WS 端点（pkgutil 自动发现）+ lifespan + 路由装配
```

依赖方向（低 → 高）：`common` / `components`（框架基座，**不** import modules/services）→ `modules`（领域模型与契约）→ `api/v1`（端点）/ `services`（服务层）→ `main.py`，无反向。

**循环断路器**：`chat ↔ gateway`、`chat ↔ scheduler`、`chat → companion → tools.builtin → scheduler → chat` 三条 import 环全部经 `services/chat/chat_emitter.py`（`Emitter` 协议，零内部依赖）收敛；重编排器/`turn_inputs`/`agent_delegate` 经 `__getattr__` 懒加载，保证 `import services.chat` 不会触发整张服务图。

## 4. 关键设计决策

- **夜间自主活动批处理（Stage 0–4 独立事务）**：在用户本地休息窗口（0:00–5:00）调用 LLM 运行批处理流水线（画像推断、记忆整理与衰退、主动规划、自我日记）。每个阶段独立事务并具备全空回滚安全网。**为什么不作为在线 agentic 循环**：批处理在离线状态也可完成用户认知深化与记忆优化，不强依赖客户端长连接；次日生成的 CronJob 在触发派发时才执行在线与 disturbance 检查。
- **夜间批处理消化「刚结束的那个本地日」，参考时刻由 cron 单点传入**：窗口在本地 0–5 点，此刻「今天」几乎没有数据，可反思的是昨天。`_maybe_run_autonomous_activity` 用 `now` 判断本地小时窗口（避免 DST 日偏移一小时），用 `now - 1d` 算数据日，并把这个 `reference_utc` 传给 `run_nightly_pipeline`。**为什么不让流水线自己算**：两处各自推导会漂移——准入门限数昨天的消息、流水线读今天的空窗口，结果是每晚扫描都判定"无消息"提前返回，`_LAST_NIGHTLY_RUN` 永不落位，整个窗口每 5 分钟重试一次且永远不成功。
- **双压缩检查点，所有对话类型统一读路径**：LLM 上下文从最近的压缩检查点起算，检查点之前的历史不进入上下文（DB 始终保留完整历史，裁剪只在 `_history_to_messages` 读路径）。两类检查点地位对等，区别只在触发时机：
  - `compress_summary`（运行时，所有对话）：`compress_history_if_needed` 在每 turn 检测 token 超过 `context_length × 0.7` 时触发，把最旧的一块消息 LLM 压成一条摘要，持久化为 `Message(role='system', subtype='compress_summary')`。下一次 turn 从该行开始读（inclusive）——被压缩的消息不再进入 LLM 上下文。
  - `daily_summary`（夜间，仅主对话）：`run_daily_checkpoint` 在夜间自主活动阶段触发，从最近的任意类型检查点（inclusive）读到当前，调一次 LLM 压成 `Message(role='system', subtype='daily_summary', summary_date=...)`。compress_summary 行的文本被纳入摘要输入，由 LLM 融入新的 daily_summary——内容不丢失。新 daily_summary 写入后 id 更大，自动取代旧 compress_summary 成为后续读起点。
  - **跳过工具帧仅限主对话**：只有主对话每个用过工具的 turn 会落一条 `tool_summary` 顶替被跳过的帧；普通对话没有替身，一并跳过等于抹掉工作上下文。
- **TOML 模版与 Git 隔离配置管理**：Git 统一托管默认配置模版 `config.toml.example`，`components/config.py` 中不保留重复默认值与冗余注释。开发者本地配置写在 `config.toml`（已被 `.gitignore` 忽略）。系统按 `OS Env > .env > config.toml > config.toml.example` 优先级加载。
- **provider 自注册而非手动 `main.py` 引入**：`services/llm/providers/<name>/__init__.py` import 时注册到 `_REGISTRY`；新增 provider 子包即可扩展能力，无须修改 `main.py`。**代价**：注册顺序敏感——`main.py` 显式 import 触发；遗漏某 provider 则该能力静默缺失（fail-open）。
- **IPC future 按 `(user_id, call_id)` 二元键寻址 + 30s 断线缓冲期（Grace Period）**：并发用户不共享 future；网关引入 `ReplayBuffer`（容量 500 帧、TTL 60s）与 30 秒断线 Grace Period，短时网络抖动期间后台生成任务（包括 LLM 流式、形象再生成与 cron turn）保持存活且产物事件进 buffer 待重放；用户重连时经 `session.resume(last_seq)` 增量补发，宽限期超时（用户未归）后由 `_grace_cleanup` 统一回收 dispatcher、未决 future、runner 工具并取消孤儿生成与 cron turn 任务。
- **LLM bpy 代码经沙箱容器执行（worker 派生）**：worker 把种子图/body-GLB 拷进每 job 私有的 `job-io` 目录后 `docker run --rm`（`--network none` + CPU/内存限额 + 只读根 + tmpfs + 仅挂该目录），超时/取消走 `docker kill`（杀 docker CLI 不杀容器）。容器名前缀 `spiritagent-job-`、label `spiritagent-worker=1`，worker 启动按 label 清扫孤儿。**挂载约束**：`docker -v` 源路径由宿主 daemon 解析，只有 `data_dir` 是宿主 bind mount——沙箱执行器强制 io_dir 必须位于 data_dir 之下（越界即 fail-loud），一切 Blender 工作区（脚本/种子/body-GLB/输出）都必须落在 job-io。**为什么不用宿主子进程直跑**：LLM 生成的代码零消毒，直跑即 RCE 面（原不变量 11 记录的问题）。**代价**：worker 需挂 docker.sock（等效宿主 root），仅授予 worker 服务、镜像内只装 docker-cli；`[blender_sandbox] enabled=false`（默认）退回裸子进程路径，保测试与裸机开发。compose 部署形态：`backend` + `worker`（docker.sock / data / config.toml ro）+ `blender-sandbox`（`--profile build` 构建镜像，worker 按镜像名引用）三服务。
- **Outbox + LISTEN/NOTIFY 而非直推 WS**：调度器 tick 只写入事件不 await WS 发射，慢客户端不拖垮事务；副本原子认领保证单发。**为什么不直接推 WS**：WS 不可水平扩展，且无法处理后端 OOM/重启时的未发事件。
- **WS 关闭码 1008（鉴权失效）立即退出重连流程**：避免在 token 失效状态下重试 N 次把请求堆到过期账号上。**为什么不让 retry**：用户凭据问题靠重试无法恢复。
- **形象生成失败对用户返回 502 + 固定文案**：作为陪伴场景的关键路径，需向用户返回可理解的友好提示并支持重试，不暴露生图服务原始错误。**为什么不透传 provider 错误**：provider 错误体常含 URL / 部分 auth header，且用户对生图服务错误无处理能力。
- **API Key 永不离开后端（fingerprinting）**：`GET /api/user/model-config` 只返回 `sk-…XX` 形式的指纹 + `_set` 布尔。**为什么不分两条**：用户自助配置时不需要原始 key 重新输入；admin 端点单独走 `PUT /api/admin/{user_id}/model-config` 强制三字段非空。
- **错误分类管道收敛为 21 种 `FailoverReason`**：8 步优先级过滤决定恢复策略（退避重试 / 凭证轮换 / 压缩上下文 / 不重试）。**为什么不暴露原始异常**：provider 错误常含 URL / 部分 auth header / 私有 SDK 调用栈，必须脱敏。
- **图生3D 走独立 image_to_3d 服务与 provider 注册表（显式指定，不做商业供应商 failover）**：独立于通用 LLM 模块（`services/image_to_3d/`），`ImageTo3DProvider` ABC（submit / poll / download 三段式 + `SUPPORTS_RIGGING` / `SUPPORTS_MULTIVIEW` 能力开关），供应商经 `services/image_to_3d/providers/<name>/` 自注册，编排统一在 `run_model_gen_pipeline`，按能力开关分支（无云端绑骨能力的供应商产物由本地 Blender 后处理补齐）。供应商选择**仅显式**：`image_to_3d_provider` 配置默认或请求 `provider` 参数覆盖；key 缺失（无论显式还是默认）→ 400 拒绝且不建生成行，桌宠以静态精灵方式交互。若当前配置的供应商不支持多视图（`SUPPORTS_MULTIVIEW is False`），`fullbody_mode` 自动回退为 `single`，避免在引导流程中浪费生成侧背图（Tripo 与混元 3D 均支持多视图）。**为什么不做供应商间自动回退**：商业 3D 生成按次计费且风格差异大，静默换供应商会让用户拿到与预期不符的模型；配额耗尽等失败直接 `model.failed` 由用户重试。**Blender 的定位是 3D 后处理工具**（morph 注入、自动绑骨、garment 换装几何），不承担模型生成——LLM 自由写 bpy 建模的质量（无 PBR）与时长（分钟级起步）都不适合作为生成主路或兜底。
- **图生3D 先落库再下载（已付费结果绝不丢）**：生成成功的一刻、下载开始前，provider task id 与下载 URL 已持久化进模型行（下载互斥锁也是行状态：`pending_download → downloading` 的 CAS 防止管线与手动重试并发下载）。下载阶段的任何失败只置 `download_failed`，不删除不回滚——恢复走"仅重试下载"路径（provider **查询**接口刷新过期签名 URL + 下载 + 后处理，绝不重新提交生成），且必须经 render 队列在 worker 执行（后处理需要 Blender，web 进程不跑）。下载内置有限次指数退避自动重试：网络类错误与 5xx 重试，403 视为签名过期走 URL 刷新，其余 4xx 不自动重试。服务重启时中断中的下载同样转入该可恢复态，而非判死。**为什么不把下载失败并入 `failed` 终态**：生成已计费，让用户"重新生成"等于二次付费且静默丢弃结果。
- **SSRF 保留段检查默认严格、按部署显式豁免（`SSRF_ALLOWED_CIDRS`）**：fake-ip TUN 代理（Clash 类）把所有域名解析进 198.18.0.0/15，`ipaddress` 判 is_private=True，令 provider 产物的对象存储下载在 SSRF 检查被拦——API 调用走普通客户端不受影响，唯独下载失败且已计费。命中豁免网段的 IP 跳过保留段拒绝，但域名黑名单、协议白名单、HTTPS→HTTP 降级拦截、云元数据/CGNAT 拦截无条件保留。**为什么不默认放行 198.18.0.0/15**：多租户部署里该段同样可能是真实内网；"本部署跑在 fake-ip 代理后"是部署者的知识，豁免必须是显式配置而非代码默认。
- **全身图 prompt 按类人面孔分流风格 + 强制 A-pose + 最小覆盖内衣（仅 biped）**：`_FULLBODY_SHARED_RULES` 以 style × rig 矩阵组织——类人面孔（人类/精灵预设 + 自定义物种由 `classify_species` 的 LLM `has_humanoid_face` 判定）走 anime 分支，biped 用"3D 日系二次元手办 CGI"措辞（**为什么手办而非平面立绘**：Tripo image-to-3D 需要种子图自带体积/法线线索，平面 cel 插画重建的几何偏软）；类人非双足（人鱼等）用二次元 + 物种纹理措辞；非人生物（机甲/灵兽/幻形/四足等，无恐怖谷问题）回退写实分支（"写实风格，8K高清"）。风格随资产持久化：`_write_fullbody` 写入 `AvatarAsset.prompt_json.fullbody_style`，`generate_companion_model` 优先读该标记写入 `CompanionModel.style`（**为什么不二次分类**：两次 LLM 调用可能给出不同 verdict，种子图与 3D 模型风格漂移会重现风格断层），随 `model.ready` 事件与 `GET /api/companion/model` 下发供客户端路由 NPR/PBR 渲染。`_BIPED_A_POSE` 保留 A-pose（双臂微张 30°）—— Tripo + Mixamo 自动绑骨强依赖对称骨架，姿态偏离会导致肩/肘/腕识别失败、权重蒙皮错位；同时要求种子图穿运动内衣+运动短裤、显式禁掉长袖/连体紧身衣/长裤/长裙/长袍/外套/靴袜（**为什么**：Tripo image-to-3D 只重建实际可见的皮肤——覆盖款哪怕紧身款会让 PBR 换装后暴露色差/反光异常/细节缺失，长裙款直接几何穿模）。
- **用户可见图像风格分层（半身像 / 精灵 = 写实；全身种子图 = 按类人面孔分流）**：半身像由 `_AVATAR_SYSTEM_PROMPT`（固定 photorealistic）持续保持写实；精灵是 GLB 缺位时的桌面降级渲染源（用户可见），主体参考切换为半身像且 `_SPRITE_PROMPT_SYSTEM` 显式锁"写实人像"措辞，使精灵与半身像保持视觉一致；全身种子图为 pipeline 内部消费（喂 Tripo3D），类人面孔走二次元手办（避免写实人像在 3D 重建后落入恐怖谷），非人生物保持写实。换装纹理提示词同路由（`_resolve_style`：active model 优先，缺行时按物种预设映射）——anime 角色的 albedo 加"二次元干净色块"措辞（toon 渲染会放大写实噪点），normal 等技术通道保持风格中性。**为什么不删精灵**：GLB 渲染前的"等不到 GLB"窗口期仍需要一张静态图占位，精灵是最稳的可见层。**为什么不让全身图也是用户可见的二次元**：会破坏半身像-精灵的写实身份锚点一致性。
- **全身种子图专用 provider 优先级（gemini → grok → minimax）**：`generate_fullbody` 用 `_FULLBODY_PROVIDER_PRIORITY = ["gemini", "grok", "minimax"]` 强制排序，与通用 image-gen 链分离。**为什么 Gemini 优先**：将种子图改为二次元风格后，`scripts/sample_fullbody_providers.py`（`--species` / `--style` 分支采样 + `backend/data/参考图.jpg`）的采样结果——Gemini 输出最贴近"anime 立绘"目标，服装/姿态/指节最稳定；Grok 姿态合规但常态画出纯白/近白色服装，会被精灵阶段的 chroma-key 误吃（配套建议在 anime 分支补一条"避免纯白色服装"，当前是后续 PBR-swap 提示词层级加固项），位列第二；MiniMax 三维渲染偏重、cel-shading 弱、最接近旧写实风，末尾兜底。**为什么不直接跟随通用 `image_gen_provider`（默认 MiniMax）**：MiniMax 对含角色文字描述的全身请求会退化为半身像、且姿态合规度最低。
- **双参考图能力仅 Gemini 支持（Grok / MiniMax 单参考）**：第二张参考图（身份锚点之外的风格/体态参考）只有 Gemini 消费——其 i2i 原生支持双参考融合（`supports_multiple_reference_images=True`）；Grok 与 MiniMax 都是单参考（MiniMax 的 subject_reference 只接受单条），收到第二张参考图时**静默忽略**。provider 链在双参考请求下把 Gemini 排在单参考供应商之前、单参考作为兜底。
- **静态精灵相册按需懒生成 + 写实锚点（[sprite_service.py](services/companion/sprite_service.py)）**：`POST /api/companion/sprite` 是无 3D 模型期的降级渲染源，主体参考锁半身像（`asset.asset_url`）而非种子图（已改为二次元），`_SPRITE_PROMPT_SYSTEM` 显式锁"写实人像"措辞与半身像风格一致；LLM 按自由语义在相册 tag 中匹配（命中零生成成本），未命中才由 LLM 撰写 prompt 经常规 image-gen 链生成（`role="waiting"` 等待图每用户唯一且命中即返、不查 LLM）。**为什么不预生成全集**：语义空间开放（状态×情绪×反应），预生成既浪费也永远不齐；懒生成让每张图都被真实需求验证。相册按 `avatar_id` 失效、上限 300 张；频控 `companion_sprite_generate_rate_limit_per_minute` 防 LLM 循环刷请求。
- **长期记忆混合检索与前置主动召回（[memory_retrieval.py](services/companion/memory_retrieval.py)）**：针对传统 SQL 子串匹配的语义鸿沟与纯被动调用的健忘问题，构建多维检索管线：
  - **Dense + Sparse 混合检索与 RRF 融合**：Dense 向量语义检索（pgvector 余弦距离，SQLite/测试环境内存回退）+ Sparse 关键词/CJK N-gram 检索，经 RRF（Reciprocal Rank Fusion，$k=60$）合并打分。
  - **艾宾浩斯时间衰减与重要性加权**：基于记忆距今时间 $\Delta t$ 实施指数衰减 $0.3 + 0.7 \times e^{-0.05 \Delta t}$（保底 0.3 防关键记忆归零），并与记忆重要度系数 $Importance \in [0.1, 5.0]$ 乘积得到最终排序得分。
  - **前置主动召回注入**：在主对话 turn 开始前自动根据当前输入匹配 Top-3 高相关 recall 记忆注入 System Prompt，免去 LLM 轮轮主动调用 `memory_recall` 的认知开销。
- **Alembic 版本化迁移 + 启动自动 upgrade**：schema 由 `alembic/versions/` 版本化；lifespan 内 `asyncio.to_thread(_run_migrations)` 自动 `upgrade head`，无独立部署步骤。**为什么启动时跑**：单实例部署且无 CI/CD，应用启动是最不易漏的迁移时机。**单一 baseline**：`0001_baseline` 是全量 schema（20 表 + 扩展 + partial unique / HNSW / GIN 索引 + 双 NOTIFY 触发器），上线前 squash 而来，此后演进只在其上追加迁移、不再重写历史。**autogenerate 两个坑**：(a) `modules.media.models`（video_gen_jobs）不被 `modules/__init__` 导入，`alembic/env.py` 的显式 import 勿删；(b) partial unique / hnsw / gin trgm 索引只存在于迁移文件（声明进模型会让 SQLite `create_all` 丢失 `WHERE` 语义变成全量唯一索引），env.py 的 `include_object` 因此跳过"仅存在于数据库"的索引——代价是从模型删除 Index 时 autogenerate 不会生成对应 `drop_index`，需手写。**严格比对**：`compare_type` + `compare_server_default` 全开，`alembic check` 必须零差异；`ModelBase.metadata` 带 naming convention（与 PG 默认约束名逐一致，新约束自动获得确定性名字，改约束名须走迁移）。
- **全异步数据访问层（SQLAlchemy 2.0 async + asyncpg 单一连接池）**：所有 session 经 `components/database.py` 的 `create_async_engine`（asyncpg 驱动，20+10 连接）与 `AsyncSession`；路由、WS handler、调度协程内的 DB 读写不再阻塞事件循环。**为什么单池**：此前同步 psycopg 池与 asyncpg LISTEN 池并存（峰值 40 连接且职责重叠），合并后 30+1。**LISTEN 专线**：`ws_event_loop` 持一条进程生命周期专用的 asyncpg 直连（LISTEN 独占连接，连接池语义无用），断线 5s 自动重连，非 PG 后端传 None 走 60s 轮询回退。**AsyncSession 纪律**：关系属性访问必须显式 `selectinload`/`joinedload`（懒加载在 async 下直接抛 `MissingGreenlet`）；`db.add` 是同步方法不可 await（`db.delete`/`commit`/`refresh` 等为 async）。**timestamptz 纪律**：全库 DateTime 列均为 `timezone=True`，`utc_now()` 返回 aware UTC；asyncpg 对 timestamptz 参数强制 aware（naive 混入在绑定期即报错）；SQLite 读回丢失 tzinfo，跨方言的 datetime 算术需容错（见 `_compute_time_decay`）。**短事务纪律（session 不跨 LLM await）**：`run_chat_turn` 不接收 session——turn 起点（加载对话 + 写用户消息 + 装配输入）一个短 session，其后每个持久化点、工具批处理前后、压缩检查点各自开短 session；`NativeMemory(db=None)` 时每次记忆工具调用自开短 session；后台任务（persona 标签刷新 / 换装 onboarding / 后台记忆复习）按"读→LLM→写"三段各持短 session，LLM 调用经预解析的 provider chain（`_chain`/`provider_config`/`vision_chain` 旁路）以 `db=None` 执行。
- **换装管线自然语言路由与分类机制**：`POST /api/companion/wardrobe/preview` 接收的描述由 LLM 语义分类为 `texture` / `garment` / `accessory` 并产出装配元数据（slot / socket / physics），分类失败默认走 `garment`（能力最全路径）。客户端不暴露 `kind` 字段——用户只输入自然语言描述，由后端自适应决定走哪条流水线。
- **换装材质防失效与自愈重生成**：`_re_sign_texture` 对已过期的临时媒体链接（`temp-media/`）安全置空，避免客户端请求失效 URL；当检测到已装备的换装项缺失材质贴图时，后台异步基于角色的当前着装描述（`outfit_description` / `prompt`，以种子图为肤色基准）触发 PBR 贴图自动重生成并持久化至 `companion-assets/`，生成完成后发射 `wardrobe.updated` 事件通知客户端静默刷新。
- **几何换装"LLM 毛坯 + 确定性后处理"分工**：LLM 负责毛坯几何生成与 `VG_ANCHOR` 锚点标注（语义理解优势），贴合/加厚/蒙皮/防穿模由确定性 bpy 算法接管（边界为 `_build_garment` 函数，保证数值几何可复现可校验）。参数表见 [MODEL_SPEC.md §4.2](../docs/MODEL_SPEC.md)。
- **挂件（Accessory）硬质绑定管线**：accessory 作为硬质附件直接挂接 socket 骨骼，无需贴合、加厚与变形蒙皮——scaffold `--kind accessory` 分支直接导出并校验 GLB 可解析。
- **骨骼 Socket 自动模糊匹配与去前缀**：针对 mixamo 导出的 `mixamorig:` 前缀差异，`_resolve_socket` 执行精确与去前缀两级匹配；匹配失败按槽位回退默认挂点（Head/RightHand/Spine2），再失败降级为 garment。
- **装备槽位互斥与 Outfit 状态拼接**：`equip` 仅顶替同 `assembly_json.slot` 的已装备部件；persona outfit 字段镜像全部已装备部件描述的文本拼接。texture 恒占 `outfit` 槽。
- **流式 Chat 一致性与错误隔离**：流式 chat 一旦首 chunk 已发不再 fallback，避免同一回合混合两个模型的输出造成上下文截断；失败统一 raise。LLM 下载临时媒体失败拦截原始 SDK 报错，返回标准短消息，避免误导性触发 LLM 回退。
- **交互频控与汇总门限**：`companion.interact` 设 5 分钟封顶作为戳击主动反应成本控制闸门，不影响自主行为与统计；`interaction_stats` 采用 OR 门限（poke/chat_turn 达到 10 即写汇总），序列化 `hour_counts` 供夜间反思。
- **MiniMax 内容风控快速失败**：`base_resp.status_code=1027` 映射到 `content_policy_blocked` 且 `retryable=False`，避免无意义重试白烧配额。

## 5. 与外部的契约

| 契约 | 方向 | 在哪定义 |
|------|------|---------|
| 伙伴层 JSON-RPC 方法（onboarding / avatar / companion / model / tts） | 对 Client | [PROTOCOL.md §1.2](../PROTOCOL.md) |
| 事件类型（`companion.affect` / `model.ready` / `wardrobe.updated` 等） | 对 Client | [PROTOCOL.md §1.3](../PROTOCOL.md) |
| Affect emotion / locale 枚举 | 对 LLM 提示词 → Client 渲染 | [PROTOCOL.md §1.4](../PROTOCOL.md) |
| 资产 URL 5 分钟 HMAC 签名 | 对 Client | [PROTOCOL.md §1.5](../PROTOCOL.md) |
| 错误信封 `{error, reason, status}` + JSON-RPC 错误码脱敏 | 对 Client | [PROTOCOL.md §1.6](../PROTOCOL.md) |
| Reserved Keys 防注入（`user_id` / `llm_config` / `user_settings`） | 对 LLM 工具入参 | [PROTOCOL.md §5.1](../PROTOCOL.md) |
| Outbox `ws_events` LISTEN/NOTIFY 调度 | 内部（业务 / Cron → Client） | [ARCHITECTURE.md §5](../ARCHITECTURE.md) |
| IPC future `(user_id, call_id)` 键语义 + 超时 + JWT 过期兜底 | 内部（Backend ↔ Client IPC） | [PROTOCOL.md §4](../PROTOCOL.md) |
| disturbance_tier 持久化（`companion_preferences` 表，重启不丢） | 接 Client 推送 [PROTOCOL.md §1.2](../PROTOCOL.md) 的 `companion.set_disturbance_tier` | [ARCHITECTURE.md §5](../ARCHITECTURE.md) |
| LLM provider chain resolver 三层入口（`provider_for_service` / `client_for_service` / `execute_with_fallback`） | 本模块独有 | 本 README §3 |
| PROVIDER-first 配置 + Tier 1–4 回落链 | 本模块独有（Provider 自注册产物） | 本 README §2 |
| 工具三层分类（backend / memory / runner） | 本模块独有 | 本 README §2 + backend 代码 |
| `ModelGenerateRequest.provider` 取值与触发条件 | 对 Client | [PROTOCOL.md §1.2](../PROTOCOL.md) |
| `companion.model.retryDownload`（仅重试下载，绝不重新计费）与 `model.failed` 载荷的 `retry_download` / `model_id` | 对 Client | [PROTOCOL.md §1.2](../PROTOCOL.md) |
| `model.ready` / `model.gen.progress` payload `provider` 字段 | 对 Client | [PROTOCOL.md §1.3](../PROTOCOL.md) |
| `model.ready` `provider` 来源标识（`tripo_image_to_3d` / `tripo_multiview_to_3d` / `hunyuan_image_to_3d` / `hunyuan_multiview_to_3d`） | 对 Client | [PROTOCOL.md §1.3](../PROTOCOL.md) |
| `onboarding.get_state` payload `fullbody_mode`（全身图主体参考按上传 → 半身像 → 文本三级回退，不再下发表单字段） | 对 Client | [PROTOCOL.md §1.2](../PROTOCOL.md) |
| `[companion] fullbody_mode` 配置与单/多视图流水线 | 本模块独有 | 本 README §2 + [PROTOCOL.md §1.2](../PROTOCOL.md) |
| `WardrobeItem` 换装装配契约（`kind` / `slot` / `assembly_json` / `mesh_url`） | 对 Client | [PROTOCOL.md §1.7](../PROTOCOL.md) |
| `POST /api/companion/wardrobe/preview`（202 入队）/ `GET .../preview/{job_id}`（轮询）/ `confirm` | 对 Client | [PROTOCOL.md §1.2](../PROTOCOL.md) |
| `wardrobe.preview.progress/.ready/.failed` 事件 | 对 Client | [PROTOCOL.md §1.3](../PROTOCOL.md) |
| render job 状态机（queued/processing/succeeded/failed + 崩溃回收） | 对 Client + 内部 | [PROTOCOL.md §1.8](../PROTOCOL.md) |
| cron 自主回合内部事件 `cron.turn.request`（outbox 路由到持连副本） | 内部 | [ARCHITECTURE.md §5](../ARCHITECTURE.md) |

## 6. 已知限制

| 限制 | 说明 |
|------|------|
| **web 单副本语义（chat 亲和）** | 运行时 chat 会话（流式 / `chat_task`）与 IPC future 在 process-local 内存，多 web 副本需粘性 WS 且不解决迁移互斥（lifespan 自动迁移无并发锁）；in-memory rate limit（slowapi）。`disturbance_tier` 与 cron 回合派发已跨副本安全。Render Worker 无进程内用户状态，可加副本 |
| **AsyncSession 关系懒加载不可用** | 关系属性在查询后访问必须显式 `selectinload`/`joinedload` 预加载，否则运行时抛 `MissingGreenlet`；新增跨表访问时需同步补加载选项。 |
| **MiniMax 视频 URL 短时效** | video_gen v2（H3）`poll` 直接返回 `download_url`，v1（Hailuo）还有 `files/retrieve` 第二跳；两者 URL 都是短时效的，必须**立即下载落 `data_dir/temp-media`**，不能直接返给前端 |
| **Cron kick 守卫** | 写行前的 `is_quiet` 守卫（tier 落库）；`cron.turn.request` 行只被持有该用户 WS 的副本认领执行，全副本离线时 10min GC 兜底清行——该次触发丢弃（`next_run_at` 已 CAS 前移，等下次调度） |
| **自动绑骨为包围盒模板** | 无云端绑骨能力的供应商（hunyuan）产物用确定性比例模板（`rig_layout`）+ ARMATURE_AUTO 自动权重本地绑骨；对非标准姿态（蜷缩、翅膀张开）效果有限，动画质量依赖模板近似。绑骨失败直接判任务失败，不产出无骨模型。 |
| **hunyuan 支持单图与多视图生3D** | 腾讯混元生3D（TokenHub 接入）支持单图及多视图（`multi_view_images`）输入；无云端绑骨能力，产物骨骼由本地 Blender 自动绑骨后处理补齐。 |
| **贴图换装受种子图皮肤可见度约束** | `kind=texture` 的 PBR 贴图换装受限于 Tripo 重建的皮肤区域——紧身覆盖款换到露出款会有色差/反光异常。`kind=garment` 的几何换装不受此约束（服装是独立 mesh，不走身体纹理迁移）。 |
| **几何服装管线需 Blender + 较长生成时间** | `kind=garment` / `accessory` 经 LLM→Blender→evaluate 迭代生成几何，单次预览耗时数分钟（受 `blender_llm_max_iterations` × `blender_llm_timeout` 支配）；预览已异步化（202 + 轮询/事件，见 [PROTOCOL.md §1.8](../PROTOCOL.md)），HTTP 不再阻塞。garment GLB 导出复用身体 armature 保证关节一致，客户端零映射 rebind。 |
| **worker 挂 docker.sock = 宿主 root 面** | 沙箱执行器经 docker.sock 派生容器，持有该 socket 等效宿主 root；仅 compose `worker` 服务挂载、镜像内只装 docker-cli，多租户部署不得开启沙箱（`blender_sandbox_enabled=false` 时退回 worker 容器内裸 blender 子进程）。 |
| **几何拟真度天花板** | 几何是程序化/LLM 生成，偏"干净"，达不到扫描级写实；通过生成期 Blender 布料重力悬垂烘焙（20 帧静态形变固化）、5 通道 PBR 贴图（含 displacement 微表面深度）与客户端 BodyCollider 表面防穿模推移提升拟真度。扫描级写实属于商业高成本管线边界，非工程缺陷。 |
| **连发排队消息的持久化时序** | 用户快速连发时，客户端先本地合并（4s 防抖窗口，[DESIGN.md §6.6](../DESIGN.md)），再经 `prompt.submit` 的 `batch` 一次性提交——前驱消息经 `persist_extra_user_messages` 落库、末条作为当轮 user 消息；前一轮 turn 生成期间连发的消息仍会在上一轮落库后作为新 turn 批量写入。因此客户端刷新（Hydration）后，排队消息顺序位于前一轮 assistant 回复之后，与问答逻辑一致。 |

## 7. 部署与监控

### Docker Compose 部署

- **基础核心启动**（仅启动 postgres + backend + worker）：
  ```bash
  docker compose up -d
  ```
- **附带 Prometheus 观测平台启动**（一键拉起指标采集）：
  ```bash
  docker compose --profile monitoring up -d
  ```
  启动后可直接访问 `http://localhost:9090` 打开 Prometheus 查询面板，指标默认每 15s 自动抓取 `backend:10620/metrics`。
