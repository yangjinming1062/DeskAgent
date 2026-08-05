# Companion 状态机与表情契约

> Desktop 伙伴层的运行时契约。`ARCHITECTURE.md` 锁定跨模块设计意图，本文件记录 Desktop 侧独有的状态机优先级、表情覆盖、过渡语义与不能从代码结构直接读出的边界。

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

| 枚举值 | CLIP_SCENES batch | SpriteEmotion | 备注 |
|---|---|---|---|
| `happy` | 3 | ✓ | |
| `sad` | 3 | ✓ | |
| `surprised` | 3 | ✓ | |
| `excited` | 3 | ✓ | |
| `confused` | 3 | ✓ | |
| `concerned` | 3 | ✓ | |
| `shy` | 3 | ✓ | |
| `proud` | 3 | ✓ | |
| `grateful` | 3 | ✓ | |
| `playful` | 3 | ✓ | |
| `bored` | 3 | ✓ | |
| `lonely` | 3 | ✓ | |
| `sleepy` | 3 | ✓ | |
| `curious` | 3 | ✓ | |
| `embarrassed` | 3 | ✓ | |
| `apologetic` | 3 | ✓ | |
| `neutral` | (无 scene) | (无 SpriteEmotion) | **过滤掉**，不触发 EMOTIONAL |

LLM 任何 `joyful` / `happy_excited` 等未注册 token 走 `affect.py::_try_resolve` 的 neutral 回退，tag 剥离后归 `idle`。

## 3. 三档打扰（双层模型）

`disturbance_tier` 由 `setDisturbanceTier` 写入并经 `companion.set_disturbance_tier` 上报后端。**后端永远 emit 事件，由 Desktop 决定如何呈现**：

| 档位 | `companion.message` 行为 | `affect` 行为 | `speak()` TTS |
|---|---|---|---|
| `proactive` | 文本 + TTS | ✓ | ✓ |
| `normal` | 文本（气泡） | ✓ | ✗ |
| `quiet` | 抑制文本与气泡 | ✓（仍可切 EMOTIONAL） | ✗ |

`is_screen_locked` 等同 `quiet`（plan §5.5 / §5.2 锁屏静默）。

**双层档位模型**（`companion-store.ts`）：`$userPreferredTier` 是用户手动选择的源真值；活动感知器写入 `$effectiveTierOverride`；其余模块读 `$effectiveTier = override ?? preferred` 做静默判定。设置面板 / chat-dock 仍显示 user_preferred。**手动 quiet 永远不被覆盖**（manual lock-in）通过 atom 内部的 `preferred === 'quiet' ? 'quiet' : ...` 短路保证。失败回滚：若后端拒绝新档位，Desktop 回滚 `$userPreferredTier` 到旧值并写 dev log。

**活动感知降级**：30s 轮询 `system.get_focused_app` + `system.is_fullscreen`（[activity.ts](activity.ts)）。当分类进入 `ide`/`gaming`/`reader` 或 `is_fullscreen` 为真时，覆盖 effective 为 quiet；focus 清除后 5s 节流推回 user_preferred。

## 4. 精灵资源降级

每 scene 计算 `active_tier = max(就绪档)`（3 > 2 > 1，不落库）。Tier 1 永远兜底——即使 zero 视频仍可启动（ARCH §10#9）。

| Tier | 形态 | 渲染 |
|---|---|---|
| 1 | 程序化 CSS 变换（呼吸 / 说话浮动 / 思考倾斜 / 睡眠漂移） | 由 `companion-ready.tsx::proceduralKey()` 选择 |
| 2 | 单张多帧 sprite PNG | `KeyframeSprite` 步进 |
| 3 | 图生视频（i2v） | `<video autoplay loop muted playsinline>` |

clip 通过 `clip.updated` 事件单通道下发。`video_gen.*` 事件由 companion 抑制（`enqueue_video_job(emit_event=False)`）。Tier 1 不可用时回退 idle loop + 状态徽章，用户永不见空白。

## 5. 屏锁与端忙

- `companion/activity.ts` 每 30s 调 `system.is_screen_locked`（`runnerInvoke`）。结果写入 `$screenLocked` atom。
- `$screenLocked.get() === true` 视同 quiet：抑制主动消息文本；affect 仍由 Backend 推送（`companion.affect` 事件，quiet 档透传 + `companion.check_affect` idle 触发 LLM 推理）。
- 屏锁恢复后静默恢复，**仅在降级曾被表达过**时补发"回神" reaction（目前实现为静默恢复，与 §4.5 文案一致）。

## 6. 自主行为（IDLE 时）

- **微动作**：10–25s 随机间隔切 `idle` / `idle_look_around` / `idle_blink` / `idle_stretch` scene。已加入 CLIP_SCENES batch 2，可升档到 Tier 2 / 3。
- **情境动作**：基于 `$focusContext`（[activity.ts](activity.ts) 维护）。focused-app 分类（ide/music/reader/gaming/browsing/other/unknown）按平台白名单映射（Windows 进程名、macOS bundle id、Linux class 名）。IDLE 微动作池按分类切换：

  | 分类 | 微动作池（未就绪 fallback 到 `idle`） |
  |------|----------------------------------|
  | ide | `idle_thinking` → `idle_typing` → `idle_look_around` → `idle` |
  | music | `idle_bounce` → `idle_sway` → `idle_blink` → `idle` |
  | reader | `idle_calm` → `idle_look_around` → `idle` |
  | gaming | `idle_engaged` → `idle_stretch` → `idle` |
  | 其他 | 沿用既有 `idle_look_around` / `idle_blink` / `idle_stretch` |

  全部走 batch 2 `CLIP_SCENES` 新增的 6 个场景；Tier 2/3 未就绪时安全 fallback 到 `idle`，符合 §1.3 "永不空白" 不变量。

## 7. 用户直接交互

- **戳**（`onTap`）：[interaction.ts](interaction.ts) 零延迟本地池（`POKE_LIGHT`/`MEDIUM`/`HEAVY`，按 tone 选）+ **可选 LLM 增强**（`companion.interact` 同步 request-response）。LLM 响应不打断本地 TTS，仅作文本气泡叠加并缓存入 tone-keyed 队列（下次同 tone 单次戳优先用缓存）。后端 throttle 1.5s，Desktop debounce 2s，per-user inflight 取消。
- **拖**（`onDragEnd`）：`interacting` 瞬态 + 拖放反应文案 + fire-and-forget `companion.record_interaction_stats {kind: 'drag'}`。
- **悬停**：10s 节流，`interacting` 1.5s。
- **右键**：托盘菜单入口（声音切换、伙伴设置、登出）。

**每日互动统计**：poke / drag / chat_turn 三类事件经 `companion.record_interaction_stats`（无 LLM）上报，Backend 按 UTC 自然日聚合 + 双门限（每类 ≥ 10）upsert `Memory(context="interaction_stats:<date>")`，喂给后续 LLM "用户当日活跃度 + 高峰时段" 信号。

## 8. cron 主动陪伴链路

`cron.trigger` 事件 + 后端自主 turn 入口（`services/scheduler/cron.py::_kick_autonomous_turn`）实现完整链路：

1. Cron CAS 赢得本 tick
2. 写 `cron.trigger` WSEvent + 启动 `_kick_autonomous_turn` task
3. 任务用用户最后 session + JsonRpcEmitter + 用户 dispatcher
4. LLM 可调 `send_message_tool(affect=...)` 产出 `companion.message`
5. Desktop 按 §3 三档规则消费

用户离线（无 dispatcher）→ 任务静默跳过，事件留 outbox 等待重连。

## 9. 不能从代码结构直接读出的边界

- **3 种 token 通过守卫**：
  - `STT` 数据 > 25 MiB → runner 端拒绝（`audio_io.DEFAULT_MAX_INPUT_BYTES`）
  - `TTS` 文本 > 4000 字符 → 拒绝
  - `runner:invoke` 60 次/秒 token bucket
- **持久化键**：
  - `da.companion.voiceId` / `da.companion.responseMode` / `da.companion.disturbanceTier` / `da.companion.chatDockOffset`
  - 仅 `disturbanceTier` + `chatDockOffset` 跨重启保留；`voiceId` 在 onMount 由 `voice-validity.ts` 校验 provider 目录变化。
- **角色编辑双路径**：`PersonaSection`（表单式直接改 6 个字段）+ `PersonaRetune`（[persona-retune.tsx](persona-retune.tsx) 5–6 步对话式 wizard 含 user_*），后者单 PUT 收尾、保留 `is_complete=True`、不重置 `is_complete`、修复前者静默 `deriveSpeakingStyle` 覆盖 `speaking_style` 的坑。
- **`/api/companion/asset/*` 文件路由**：已切到 HMAC 签名 URL（`user_id` + `filename` + 5 分钟 expiry + HMAC），后端 `verify_signed_asset_request` 强制校验，丢签名 401。Asset 落持久目录（`companion-avatars/` / `companion-assets/`），URL 一次性 5 分钟有效。
- **CORS / 跨窗口**：精灵窗口与对话面板共享同一 Electron 渲染进程（panel 是 React child of sprite window），`setAlwaysOnTop` 不再被 chat-dock 切换。
