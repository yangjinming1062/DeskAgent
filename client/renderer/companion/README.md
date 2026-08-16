# Companion 状态机与表情契约

> Client 伙伴层的运行时契约。`ARCHITECTURE.md` 锁定跨模块设计意图，本文件记录 Client 侧独有的状态机优先级、表情覆盖、过渡语义与不能从代码结构直接读出的边界。

## 1. 动画状态机（9 态）

| 状态 | 优先级 | 触发源 | 持续 |
|---|---|---|---|
| `disconnected` | 100 | Backend WS 断连 | 持续；恢复需 WS 重连 + 5min grace 后升级为 `sleeping` |
| `interacting` | 80 | 用户戳 / 拖 / 悬停 | 瞬态 1.5–2.0s，回到 `previousState` |
| `working` | 70 | 用户活动 ≥ 6 次/10s | 持续；10s 无活动 `force: true` 回 `idle` |
| `speaking` | 60 | TTS 播放 | 与 TTS 音频等长 |
| `thinking` | 50 | LLM 流式响应开始 | 持续至 `message.complete` |
| `listening` | 40 | 用户开始输入 | 持续至用户停止输入或后端响应 |
| `emotional` | 35 | `affect` cue 到达 | 瞬态 2.5s，回到 `previousState`（**叠加非抢占**） |
| `sleeping` | 30 | 深夜 23:00–7:00 或长断连 | 持续；poke / chat-dock 打开 → `wakeUpFromSleep()` |
| `idle` | 10 | 默认 | 持续；10–25s 随机切 IDLE 变体 |

### 1.1 状态切换规则

- **低优先级不能打断高优先级**（`setSpriteState(name)` 默认 `force: false`）—— 已在 `working` 时调用 `setSpriteState('idle')` 被门控逻辑直接吞掉。需强制回退必须传 `{ force: true }`（working 状态 10s 无活动自动 force 回 idle）。
- **`emotional` / `interacting` 是叠加而非抢占**：进入前若当前不是这两个状态，原子 `$previousState` 记录原态；瞬态 timer 结束后回到 `previousState`（若 prev 也是 emotional/interacting，则回 `idle`）。
- **crossfade ~250ms**：clip 切换通过 sprite-stage 的 fade 层处理，避免硬切。

### 1.2 EMOTIONAL 帧时机（ARCH §6.3）

`message.complete` 帧内联 `affect: {emotion}` 字段。当 emotion 存在且 ≠ `neutral`：

1. 立即 `setSpriteState('emotional', { emotion })`
2. 若 `responseMode === 'voice'`，**延迟 1.2s** 后再 `setSpriteState('speaking')` + `speak()` —— 让 EMOTIONAL 帧可见
3. 若 `responseMode === 'text'`，直接进入下一句渲染，不强制 speaking 状态

`emotion === 'neutral'` 不触发 EMOTIONAL 状态，直接回 `idle`。这是 LLM 的"无特定情绪"答案，不是"中性情绪"。

## 2. 表情枚举

| 枚举值 | MorphController | 备注 |
|---|---|---|---|
| `happy` | ✓ | |
| `sad` | ✓ | |
| `surprised` | ✓ | |
| `excited` | ✓ | |
| `confused` | ✓ | |
| `concerned` | ✓ | |
| `shy` | ✓ | |
| `proud` | ✓ | |
| `grateful` | ✓ | |
| `playful` | ✓ | |
| `bored` | ✓ | |
| `lonely` | ✓ | |
| `sleepy` | ✓ | |
| `curious` | ✓ | |
| `embarrassed` | ✓ | |
| `apologetic` | ✓ | |
| `neutral` | (无) | **过滤掉**，不触发 EMOTIONAL |

LLM 任何 `joyful` / `happy_excited` 等未注册 token 走 `affect.py::_try_resolve` 的 neutral 回退，tag 剥离后归 `idle`。

## 3. 三档打扰（双层模型）

`disturbance_tier` 由 `setDisturbanceTier` 写入并经 `companion.set_disturbance_tier` 上报后端。**后端永远 emit 事件，由 Client 决定如何呈现**：

| 档位 | `companion.message` 行为 | `affect` 行为 | `speak()` TTS |
|---|---|---|---|
| `proactive` | 文本 + TTS | ✓ | ✓ |
| `normal` | 文本（气泡） | ✓ | ✗ |
| `quiet` | 抑制文本与气泡 | ✓（仍可切 EMOTIONAL） | ✗ |

`is_screen_locked` 等同 `quiet`（plan §5.5 / §5.2 锁屏静默）。

**双层档位模型**（`companion-store.ts`）：`$userPreferredTier` 是用户手动选择的源真值；活动感知器写入 `$effectiveTierOverride`；其余模块读 `$effectiveTier = override ?? preferred` 做静默判定。设置面板 / chat-dock 仍显示 user_preferred。**手动 quiet 永远不被覆盖**（manual lock-in）通过 atom 内部的 `preferred === 'quiet' ? 'quiet' : ...` 短路保证。失败回滚：若后端拒绝新档位，Client 回滚 `$userPreferredTier` 到旧值并写 dev log。

**活动感知降级**：30s 轮询 `system.get_focused_app` + `system.is_fullscreen`（[activity.ts](activity.ts)）。当分类进入 `ide`/`gaming`/`reader` 或 `is_fullscreen` 为真时，覆盖 effective 为 quiet；focus 清除后 5s 节流推回 user_preferred。

## 4. 3D 渲染资源降级与功耗调度

渲染栈是 `three/webgpu` 的 **WebGPURenderer + 四层回退**：WebGPU 后端 → three 内置 WebGL2 后端（同 API 面，零代码）→ 经典 `WebGLRenderer`（仅当 `init()` 整体 reject；必须换新 canvas——webgpu 上下文成功过的 canvas 要不到 webgl2）→ `EngineInitError`（静态精灵层兜底）。`Engine.create()` 是异步工厂，canvas 由 Engine 自建自管（React 只渲染容器），companion-3d 的 load/outfit effects 一律 await 引擎就绪 Promise；实际后端写 dev log。

GLB 加载成功后骨骼动画覆盖全部状态；GLB 不可用（生成中/失败/无 key/换模空挡）时**静态精灵模式**接管——[static-sprite/](static-sprite/) 的 `StaticSprite` 层叠在 canvas 之上，有图显示后隐藏 canvas（蛋不透出），GLB 真正解析完成（`LoadedModelInfo.procedural === false`，而非 `model.ready`——该事件早于字节落地，靠此修掉换模闪蛋）才淡出交还。相册不可用/图未到达时蛋继续显示（永不空白）。语义映射表在 [sprite-semantics.ts](static-sprite/sprite-semantics.ts)（9 状态 + 17 情绪 + 未覆盖情绪的通用回退子句）；请求去重/1.5s 间隔/`content_hash` 内存缓存在 [sprite-store.ts](static-sprite/sprite-store.ts)——失败保持当前图。图间 250ms 淡切，`prefers-reduced-motion` 下禁用淡切与呼吸。加载失败时渲染程序化兜底角色（Three.js 基本体组合 + 正弦驱动呼吸/眨眼/说话浮动）。MorphController 经语义别名映射到 GLB 内置的 ARKit morph targets——缺失的情绪 morph 静默回退为 idle 面部。模型经 \model.ready\ 事件下发，换装经 \wardrobe.updated\ 下发。

**渲染功耗三档**（[3d/PowerProfile.ts](3d/PowerProfile.ts) 判定 + [3d/power-signals.ts](3d/power-signals.ts) 订阅，Engine 自门控循环执行）：主进程为后台流式聊天全局禁用了 Chromium 节流，浏览器不会替 7x24 常驻的精灵窗降频，所以循环在 Engine 内按信号自门控——active 60fps（speaking/thinking/listening/working/emotional/interacting）、idle 30fps（idle/disconnected）、dormant 4fps（sleeping 状态、`$screenLocked`、`document.hidden`、`$focusContext.fullscreen`、static-sprite 完全覆盖）。信号全部来自既有渲染端 atom，功耗调度是纯 Client 内部决策（ARCH §7 语义/渲染解耦），无协议与主进程参与。

两条防坑约束：**Ready 保护**——首个模型 `loadCharacter` 落定（`$modelLoadSettled`）前强制 active，避免孵化动画被误降频拉长；**隐藏窗口降级**——Chromium 对 hidden 窗口硬停 rAF（禁节流开关管不到合成层），active/idle 档在 `document.hidden` 时改由 16/37ms timer 驱动，`visibilitychange` 恢复 rAF。dormant 恒为 250ms timer（进程级禁 timer 节流，锁屏下稳定）；档位回升时 Engine 层把 delta 钳制到 50ms，防 mixer/布料在长暂停后跳变。

## 5. 屏锁与端忙

- `companion/activity.ts` 每 30s 调 `system.is_screen_locked`（`runnerInvoke`）。结果写入 `$screenLocked` atom。
- `$screenLocked.get() === true` 视同 quiet：抑制主动消息文本；affect 仍由 Backend 推送（`companion.affect` 事件，quiet 档透传 + `companion.check_affect` idle 触发 LLM 推理）。
- 屏锁恢复后静默恢复，**仅在降级曾被表达过**时补发"回神" reaction（目前实现为静默恢复，与 §4.5 文案一致）。

## 6. 自主行为（IDLE 时）

- **微动作**：10–25s 随机间隔切 `idle` / `idle_look_around` / `idle_blink` / `idle_stretch` scene。由 3D 引擎骨骼动画直接驱动，不需生成资产。
- **情境动作**：基于 `$focusContext`（[activity.ts](activity.ts) 维护）。focused-app 分类（ide/music/reader/gaming/browsing/other/unknown）按平台白名单映射（Windows 进程名、macOS bundle id）。IDLE 微动作池按分类切换：

  | 分类 | 微动作池（未就绪 fallback 到 `idle`） |
  |------|----------------------------------|
  | ide | `idle_thinking` → `idle_typing` → `idle_look_around` → `idle` |
  | music | `idle_bounce` → `idle_sway` → `idle_blink` → `idle` |
  | reader | `idle_calm` → `idle_look_around` → `idle` |
  | gaming | `idle_engaged` → `idle_stretch` → `idle` |
  | 其他 | 沿用既有 `idle_look_around` / `idle_blink` / `idle_stretch` |

  每个变体对应 GLB 内置的一个骨骼动画 clip；模型未提供该 clip 时引擎回退到 `idle`，符合 §1.3 "永不空白" 不变量。

## 7. 用户直接交互

- **戳 / 拖**（`onTap` / `onDragEnd`）：[interaction.ts](interaction.ts) 从 `client/renderer/companion/reactions/manifest.json` 的 52 条文案里按 (bucket, tone) 随机抽一条（[reactions/reaction-audio.ts](reactions/reaction-audio.ts)），交给 `speakScripted`（[tts.ts](tts.ts)）→ `spiritagent:media:tts { persist: true }`。合成结果按 `sha1(音色 + 台词)` 内容寻址缓存在 `$SPIRITAGENT_HOME/audio/tts-cache/<lang>/`：首次播放合成一次并落盘，之后都是本地读盘，同一组 (音色, 台词) 一辈子只花一次云端额度。换音色或改台词会让缓存键变化从而自然失效，没有需要维护的失效逻辑；只有云端结果落盘，Piper 兜底产物不写，否则它会冒充用户选定的云端音色。音色试听句走同一条路径。
- **悬停**：10s 节流，`interacting` 1.5s（不放音）。
- **右键**：托盘菜单入口（声音切换、伙伴设置、登出）。

**每日互动统计**：poke / drag / chat_turn 三类事件经 `companion.record_interaction_stats`（无 LLM）上报，Backend 按 UTC 自然日聚合 + OR 门限（任一类 ≥ 10）upsert `Memory(context="interaction_stats:<date>")`（含 hour_counts 快照），喂给后续 LLM "用户当日活跃度 + 高峰时段" 信号。

## 8. cron 主动陪伴链路

后端自主 turn 入口（`services/scheduler/cron.py::_kick_autonomous_turn`）实现完整链路：

1. Cron CAS 赢得本 tick，直接启动自主回合 task（无 WSEvent 中转）
2. 任务用用户最后 session + JsonRpcEmitter + 用户 dispatcher
3. LLM 可调 `send_message_tool(affect=...)` 产出 `companion.message`
4. Client 按 §3 三档规则消费

用户离线（无 dispatcher）→ 任务静默跳过；`next_run_at` 已被 CAS 推进，下一个调度周期重新到期后再尝试。

## 9. 不能从代码结构直接读出的边界

- **3 种 token 通过守卫**：
  - `STT` 数据 > 25 MiB → runner 端拒绝（`audio_io.DEFAULT_MAX_INPUT_BYTES`）
  - `TTS` 文本 > 4000 字符 → 拒绝
  - `runner:invoke` 60 次/秒 token bucket
- **Stop 按钮双通道**：`session.interrupt`（停 LLM 流）+ `runnerCancel`（置 Runner 全局中断标记，让在跑的本地工具尽早退出）；两者均为 best-effort，本地 finalize 兜底 UX
- **持久化键**：
  - `da.companion.voiceId` / `da.companion.responseMode` / `da.companion.disturbanceTier` / `da.companion.chatDockOffset` / `da.companion.defaultScale`
  - 仅 `disturbanceTier` + `chatDockOffset` + `defaultScale` 跨重启保留；`voiceId` 在 onMount 由 `voice-validity.ts` 校验 provider 目录变化。
  - 精灵位置持久化在 `companion-position.json`（Electron userData 目录，非 localStorage）。
- **角色编辑双路径**：`PersonaSection`（表单式直接改 6 个字段）+ `PersonaRetune`（[persona-retune.tsx](persona-retune.tsx) 5–6 步对话式 wizard 含 user_*），后者单 PUT 收尾、保留 `is_complete=True`、不重置 `is_complete`、修复前者静默 `deriveSpeakingStyle` 覆盖 `speaking_style` 的坑。仅 `PersonaSection` 保存后会接入两步形象再生成（先头像 → 用户确认 → 全身）；`PersonaRetune` 是纯 persona 维度调整，不重跑形象流水线。Onboarding 自身始终走两步 UI。
- **链式参考形象生成**：步 1 `POST /api/companion/avatar`（或 `/from-image`）只产半身头像；步 2 分阶段调 `POST /api/companion/avatar/{id}/fullbody`——先 `stage='front'` 以头像为参考生成正面全身，确认后再 `stage='aux'` 以正面全身为参考并发生成侧面与背面；单视角重绘用 `view='front'|'right'|'back'`，重绘正面自动失效侧面/背面。`useRegeneratePortrait` 只负责头像重生（步 1），全身生成在 onboarding 内直接走 REST；`avatar-regen-store.ts` 仅保留 `avatar.regenerated` awaiter。
- **Onboarding 单/多视图分支**：`onboarding.get_state` 返回 `fullbody_mode`（由 Backend `[companion] fullbody_mode` 配置决定），renderer 据此跳过或保留 right/back 两阶段。Bootstrap 期间 renderer 在 mode 未知时显示加载占位，避免先以错误模式渲染文案造成闪烁。
- **Wardrobe Studio**（[wardrobe-design.tsx](wardrobe-design.tsx)）：设置页「打开换装设计」入口。流程 = `POST /api/companion/wardrobe/preview`（202 入队，客户端轮询 `GET /wardrobe/preview/{job_id}` 至 succeeded——后端用一次 LLM 路由调用决定走 `texture`（仅改色/材质/图案，数秒）、`garment`（LLM+Blender 几何装配，数分钟）或 `accessory`（挂件挂到 socket 骨骼）流水线 → 落到 temp-media 拿到 `file_id` / `mesh_file_id`）→ Client 把预览产物通过 `$wardrobePreview` 临时挂到 3D 模型上做实时切换（`CharacterController.setOutfit` 按 `kind` 自动分派贴图热替或几何装配）→ 候选回溯（`$wardrobeCandidates`，cap = 3，shift-out）→ 用户点「确认入衣柜」触发 `POST /api/companion/wardrobe/confirm` 落 `WardrobeItem(category='generated')` + 自动装备 + emit `wardrobe.updated`。可附参考图（base64 upload）+ 反馈文本（每轮 `feedback` 进入 LLM payload 用于修正）。Discard / 关闭不主动清理 temp-media，由服务端 `cleanup_expired`（`temp_file_ttl_hours`）兜底。预览是**槽位感知**的：预览候选只替换同槽已装备件，其余槽位继续渲染（试穿上衣不会视觉上脱掉已穿的鞋）。
- **几何换装装配层**（[CharacterController.ts](3d/CharacterController.ts)）：装备集是多单元数组——texture 项热替身体材质（空槽时清空残留 PBR），garment/accessory 项按 `assembly_json.layer` 升序装配为独立 unit。garment rebind：载入 GLB → 取身体 SkinnedMesh 的 skeleton + bindMatrix → `mesh.bind(bodySkeleton, bodyBindMatrix)` + `DetachedBindMode`（避免被误加进 skeleton 根骨骼二次变换），丢弃 GLB 自带 armature（身体 skeleton 是唯一动画源）；关节顺序不一致时（导出端回归信号）按骨骼名重映射 `geometry.skinIndex` 兜底。accessory attach：在 socket 骨骼下挂一个补偿 anchor（`matrix = inverse(bone.matrixWorld)`），mesh 保持导出时的世界摆放同时继承骨骼运动；socket 找不到时退化为静态摆放并打告警。PBR 贴图绑定作用域收窄到**该单元自己的 mesh**（材质 `"*"` 通配），不会污染身体。epoch 守卫同时覆盖贴图与几何两类异步加载（加载 A 时切到 B，迟到的 A 回调不得再装配）。
- **布料求解器双后端**（[physics/](3d/physics/)）：`PhysicsBackend` 抽象下两条实现共享 [cloth-topology.ts](3d/physics/cloth-topology.ts) 提取的拓扑单一事实源（锚环 30%、索引去重边 + rest 长度、骨骼球半径表、16K 顶点预算）——**CpuBackend**（WebGL2 / 经典 WebGL 回退路径，[ClothSolver.ts](3d/ClothSolver.ts) 主线程 verlet + [BodyCollider.ts](3d/BodyCollider.ts) 身体表面 BVH 推出，行为与纯 WebGL 版完全一致）与 **TslComputeBackend**（WebGPU 路径，TSL compute 四连 pass：蒙皮+verlet 积分 → 距离约束×3 → 骨骼球推出 → 每 2 帧法线重算）。GPU 路径的关键契约：**storage `pos` 恒为网格局部坐标**——骨骼矩阵 / 碰撞球等世界输入在 shader 内先乘 `meshInv`（镜像 CPU 求解器的 `_inv` 模式），`MeshStandardNodeMaterial.positionNode` 读出的局部值经 RenderPass 恰好一次 `modelWorldMatrix` 变换，杜绝服装二次变换飞出视野；法线 pass 用 per-vertex 三角形邻接表（无原子写冲突）+ `max(length, 1e-6)` 钳制（退化三角形的零向量 `normalize` 会产 NaN 黑斑）；渲染**零回读**（storage 直供 positionNode/normalNode，`getArrayBufferAsync` 仅诊断用）。每帧 CPU→GPU 上传只有一份 per-skeleton 骨骼矩阵快照（~4KB）+ 小 uniforms；骨骼矩阵沿用「上一帧渲染值」的一帧滞后契约。GPU 路径的取舍：身体 mesh 级 BVH 碰撞不搬 GPU（cloth 用骨骼球、skin 单元无身体推挤，极端动作可能轻微穿模）；约束松弛在单次 dispatch 内存在共享端点竞态（三次 dispatch 间收敛，近似 Jacobi/GS 混合）；无 skinIndex/skinWeight 的网格退化为静态渲染。Web Worker 方案被否决：GPU compute 已接管主线程解耦诉求，回退机器少，先接受 CPU 主线程成本（后续可选优化）。
- **`/api/companion/asset/*` 文件路由**：已切到 HMAC 签名 URL（`user_id` + `filename` + 5 分钟 expiry + HMAC），后端 `verify_signed_asset_request` 强制校验，丢签名 401。Asset 落持久目录（`companion-avatars/` / `companion-assets/`），URL 一次性 5 分钟有效。
- **CORS / 跨窗口**：精灵窗口与对话面板共享同一 Electron 渲染进程（panel 是 React child of sprite window），`setAlwaysOnTop` 不再被 chat-dock 切换。

## 10. 空间行为（位置 × 移动 × 缩放）

设计意图见 [DESIGN.md §3](../../../DESIGN.md)。本节记录 Client 侧的实现契约。

**单一权威源**：[spatial.ts](spatial.ts) 拥有所有空间状态——`$spatialPos`、`$spatialScale`、`$spatialLocale`、`$spatialLocomotion`。sprite-stage.tsx 是纯消费者（`useStore` + 事件转发到 spatial 函数），不再持有本地位置 state。

**移动引擎**：rAF 插值（非 CSS transition），walk ≈ 80 px/s、fly ≈ 400 px/s。用户拖拽瞬时覆盖一切其他移动。任何新 `moveTo` 或 drag 自动取消正在进行的动画。

**`initSpatial()`**：在 root.tsx mount 时调用一次，注册所有空间反应——$chatOpen（home ↔ chat 动画切换）、$spriteState（sleep 位 + 自适应缩放）、$effectiveTier（空间策略 + 缩放）、$focusContext（perch 决策）。返回 cleanup 函数。

**决策树**（`updateSpatialDecision`）：drag > chat(listener) > sleeping → sleep 位 > quiet → home > 有焦点窗口几何 + tier ≠ quiet + category ∉ {unknown, gaming} + !fullscreen → perch > proactive + idle + 无 perch 目标 → roam > home。每次 tier / focus / state 变化触发重评估。

**perch 位置**：从焦点窗口几何（`$focusContext.windowGeom`）计算——优先窗口右下角外侧，右溢出则尝试左侧，两侧均溢出则放弃（窗口太宽）。perch 仅在 idle 时发起；进入 perch 后 work/think/speak 状态不踢出（"陪"语义）。

**roam**：自补充式 waypoint 循环（每个点停 5–15s），waypoint 在屏幕下半部随机生成。仅 proactive + idle + 无 perch 目标时触发。任何 drag / chat / focus / tier 变化通过 `stopRoam` 终止。

**缩放**：`$defaultScale`（用户设置，localStorage）是基准。EMOTIONAL 状态的 excited/surprised/playful 触发 1.3–1.6× 临时放大，quiet 档不放大。缩放也是 rAF 动画（~300ms），通过容器 `transform: scale()` 实现——与 sprite 内部的程序化动画（呼吸/浮动）在不同 DOM 层，不冲突。

**Backend 零感知**：所有空间决策在 Client 本地完成，无 WS 事件或 RPC 新增。Runner 提供感知能力（`system.get_windows` 窗口枚举、`system.get_focused_app` 焦点窗口几何）但 Runner 也不知道空间行为存在。

**Ritual walk**（[ritual-walk.ts](ritual-walk.ts)）：`system.open_application` 工具调用经 events.ts 拦截——执行工具后等 1.5s 窗口出现 → `system.get_windows` 按名称匹配窗口 → fly 到目标 → INTERACTING 1.5s → 返回原 locale。任一步骤失败则静默跳过（仪式是增强层）。chat 开启或屏锁时直接执行不走路。
