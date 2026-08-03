# Companion 状态机与表情契约

> Desktop 伙伴层的运行时契约。`ARCHITECTURE.md` 锁定跨模块设计意图，本文件记录 Desktop 侧独有的状态机优先级、表情覆盖、过渡语义与不能从代码结构直接读出的边界。

## 1. 动画状态机（9 态）

| 状态 | 优先级 | 触发源 | 持续 |
|---|---|---|---|
| `disconnected` | 100 | Backend WS 断连 | 持续；恢复需 WS 重连 + 5min grace 后升级为 `sleeping` |
| `interacting` | 80 | 用户戳 / 拖 / 悬停 | 瞬态 1.5–2.0s，回到 `previousState` |
| `working` | 70 | 用户活动 ≥ 6 次/10s | 持续；10s 无活动 `force: true` 回 `idle`（**P0-9**） |
| `speaking` | 60 | TTS 播放 | 与 TTS 音频等长 |
| `thinking` | 50 | LLM 流式响应开始 | 持续至 `message.complete` |
| `listening` | 40 | 用户开始输入 | 持续至用户停止输入或后端响应 |
| `emotional` | 35 | `affect` cue 到达 | 瞬态 2.5s，回到 `previousState`（**叠加非抢占**） |
| `sleeping` | 30 | 深夜 23:00–7:00 或长断连 | 持续；poke / chat-dock 打开 → `wakeUpFromSleep()` |
| `idle` | 10 | 默认 | 持续；10–25s 随机切 IDLE 变体 |

### 1.1 状态切换规则

- **低优先级不能打断高优先级**（`setSpriteState(name)` 默认 `force: false`）—— 已在 `working` 时调用 `setSpriteState('idle')` 被门控逻辑直接吞掉。需强制回退必须传 `{ force: true }`（P0-9 working 10s 退出）。
- **`emotional` / `interacting` 是叠加而非抢占**：进入前若当前不是这两个状态，原子 `$previousState` 记录原态；瞬态 timer 结束后回到 `previousState`（若 prev 也是 emotional/interacting，则回 `idle`）。
- **crossfade ~250ms**：clip 切换通过 sprite-stage 的 fade 层处理，避免硬切。

### 1.2 EMOTIONAL 帧时机（ARCH §7.5）

`message.complete` 帧内联 `affect: {emotion}` 字段。当 emotion 存在且 ≠ `neutral`：

1. 立即 `setSpriteState('emotional', { emotion })`
2. 若 `responseMode === 'voice'`，**延迟 1.2s** 后再 `setSpriteState('speaking')` + `speak()` —— 让 EMOTIONAL 帧可见（P1-13）
3. 若 `responseMode === 'text'`，直接进入下一句渲染，不强制 speaking 状态

`emotion === 'neutral'` 不触发 EMOTIONAL 状态，直接回 `idle`（P1-5）。这是 LLM 的"无特定情绪"答案，不是"中性情绪"。

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
| `sleepy` | 3 | ✓ | P1-5 新增 |
| `curious` | 3 | ✓ | P1-5 新增 |
| `embarrassed` | 3 | ✓ | P1-5 新增 |
| `apologetic` | 3 | ✓ | P1-5 新增 |
| `neutral` | (无 scene) | (无 SpriteEmotion) | **过滤掉**，不触发 EMOTIONAL |

LLM 任何 `joyful` / `happy_excited` 等未注册 token 走 `affect.py::_try_resolve` 的 neutral 回退，tag 剥离后归 `idle`。

## 3. 三档打扰

`disturbance_tier` 由 `setDisturbanceTier` 写入并经 `companion.set_disturbance_tier` 上报后端。**后端永远 emit 事件，由 Desktop 决定如何呈现**（P0-7，P1-17 跨副本路由前提）：

| 档位 | `companion.message` 行为 | `affect` 行为 | `speak()` TTS |
|---|---|---|---|
| `proactive` | 文本 + TTS | ✓ | ✓ |
| `normal` | 文本（气泡） | ✓ | ✗ |
| `quiet` | 抑制文本与气泡 | ✓（仍可切 EMOTIONAL） | ✗ |

`is_screen_locked` 等同 `quiet`（plan §4.5 / §4.2 锁屏静默）。失败回滚：若后端拒绝新档位，Desktop 回滚 `$disturbanceTier` 到旧值并写 dev log（P2-15）。

## 4. 精灵资源降级

每 scene 计算 `active_tier = max(就绪档)`（3 > 2 > 1，不落库）。Tier 1 永远兜底——即使 zero 视频仍可启动（ARCH §11#9）。

| Tier | 形态 | 渲染 |
|---|---|---|
| 1 | 程序化 CSS 变换（呼吸 / 说话浮动 / 思考倾斜 / 睡眠漂移） | 由 `companion-ready.tsx::proceduralKey()` 选择 |
| 2 | 单张多帧 sprite PNG | `KeyframeSprite` 步进 |
| 3 | 图生视频（i2v） | `<video autoplay loop muted playsinline>` |

clip 通过 `clip.updated` 事件单通道下发（P0-8）。`video_gen.*` 事件由 companion 抑制（`enqueue_video_job(emit_event=False)`）。Tier 1 不可用时回退 idle loop + 状态徽章，用户永不见空白。

## 5. 屏锁与端忙

- `companion/activity.ts` 每 30s 调 `system.is_screen_locked`（`runnerInvoke`）。结果写入 `$screenLocked` atom。
- `$screenLocked.get() === true` 视同 quiet：抑制主动消息文本，仍流 affect。
- 屏锁恢复后静默恢复，**仅在降级曾被表达过**时补发"回神" reaction（目前实现为静默恢复，与 §4.5 文案一致）。

## 6. 自主行为（IDLE 时）

- **微动作**：10–25s 随机间隔切 `idle` / `idle_look_around` / `idle_blink` / `idle_stretch` scene。已加入 CLIP_SCENES batch 2（P1-3），可升档到 Tier 2 / 3。
- **情境动作**：`system.get_idle_seconds` / `get_focused_app` / `get_power_state` 轮询直接进情境判定（**不经 LLM**），未启用——仅占位。

## 7. 用户直接交互

- **戳**（`onTap`）：根据连戳频次 `light / medium / heavy` 反应文案池。`interacting` 瞬态 2s 回到 `previousState`。
- **拖**（`onDragEnd`）：`interacting` 瞬态 + 拖放反应文案。P2-5 之后 dock 自身也可拖（chat panel 单独可拖；sprite 位置独立）。
- **悬停**：10s 节流，`interacting` 1.5s。
- **右键**：托盘菜单入口（声音切换、伙伴设置、登出）。

## 8. cron 主动陪伴链路

`cron.trigger` 事件 + 后端自主 turn 入口（`services/scheduler/cron.py::_kick_autonomous_turn`）实现完整链路（P0-5）：

1. Cron CAS 赢得本 tick
2. 写 `cron.trigger` WSEvent + 启动 `_kick_autonomous_turn` task
3. 任务用用户最后 session + JsonRpcEmitter + 用户 dispatcher
4. LLM 可调 `send_message_tool(affect=...)` 产出 `companion.message`
5. Desktop 按 §3 三档规则消费

用户离线（无 dispatcher）→ 任务静默跳过，事件留 outbox 等待重连。

## 9. 不能从代码结构直接读出的边界

- **3 种 token 通过守卫**：
  - `STT` 数据 > 24MB → 拒绝（`media.cjs:225-227`）
  - `TTS` 文本 > 4000 字符 → 拒绝
  - `runner:invoke` 60 次/秒 token bucket（P2-17）
- **持久化键**：
  - `da.companion.voiceId` / `da.companion.responseMode` / `da.companion.disturbanceTier` / `da.companion.chatDockOffset`（P1-18 + P2-5）
  - 仅 `disturbanceTier` + `chatDockOffset` 跨重启保留；`voiceId` 在 onMount 由 `voice-validity.ts` 校验 provider 目录变化。
- **`/api/companion/asset/*` 文件路由**：当前无鉴权，靠 `secrets.token_urlsafe(8)` token 熵 + 文件名白名单。**Contract P2-15**：未来需切到 signed URL（`user_id` + `filename` + `expiry` + HMAC），与 installer 一次性 bootstrap 一致的临时凭证。
- **CORS / 跨窗口**：精灵窗口与对话面板共享同一 Electron 渲染进程（panel 是 React child of sprite window），`setAlwaysOnTop` 不再被 chat-dock 切换（P1-4）。
