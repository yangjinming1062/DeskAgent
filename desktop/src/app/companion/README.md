# 伙伴层（companion）

桌面陪伴精灵的渲染与交互层。双窗口模型、跨窗口 auth 同步、gateway auth 门控等与枢纽层共享的架构决策记在 [desktop/README.md](../../../README.md)，本文件只记伙伴层**独有**的决策与契约。完整交互设计（onboarding、动画状态机、陪伴范式、渐进路线）见 [plan.md](../../../plan.md)。

## 生命周期 × 鉴权阶段 → 视觉态

`$companionLifecycle`（`store/companion.ts`）驱动精灵窗口渲染什么；`$auth` + `$gatewayState` 决定蛋的视觉态。MVP Slice 1 只走 `unauthed-egg` → `ready` 两态，`onboarding`/`hatching` 在 Slice 2 接入。蛋的视觉态按阶段映射，**不可错配语义**：

| 阶段 | 蛋视觉 | 为什么 |
|------|--------|--------|
| 未鉴权（冷启 / 已登出） | `teaser`（idle 呼吸，**不犯困**） | 犯困语义是"在等脑回话"；未鉴权无 WS，不该暗示在等 |
| 已鉴权 · gateway boot / 重连中 | `drowsy`（慢摇 + z） | 替代旧全屏 GatewayConnectingOverlay——伙伴"在醒来" |
| 已鉴权 · gateway `open` | `awake`（轻浮 + 亮 glow） | 脑在线，伙伴活了 |
| 已鉴权 · 运行中断连 | `drowsy`→重连回 `awake` | DISCONNECTED 分级降级（完整 grace 见 plan.md §4.5，后续 slice） |

## 形象资产：双模 + 永不空白

`CompanionVisual`（后续 slice）按"生命周期 + 状态 + 资产可用性"选渲染模式：

- **code-rendered（SVG/CSS）**：蛋、silhouette、以及**所有 clip 未就绪时的回退**（idle 呼吸 + 状态轻量图标：WORKING 齿轮、THINKING 省略号、DISCONNECTED 犯困）。MVP 全程此模式。
- **clip（WebM alpha）**：Backend 以 portrait 为种子生成的循环视频，就绪后接管对应状态。MVP 阶段 `$assetRegistry` 恒空 → 永走回退。

**不变量**（[design.md §11#9](../../../design.md)）：任何状态/clip 缺失必须回退 code-rendered，用户永不见"未生成"空白。这条解耦（Backend 只产 emotion 语义、Desktop 决定渲染）的代价兜底就在这里。

## Onboarding 流程（`OnboardingFlow`）

`CompanionRoot` 在已鉴权时 `GET /api/companion/persona` 按 `is_complete` 路由：未完成 → `onboarding`、完成 → `ready`。onboarding 是顺序状态机（`q` → `hatching` → `portrait` → `voice` → `finishing` → `greeting`）：

- **5 问**（plan §3.2）：silhouette 以默认中性音 TTS 念题（`deskagent:media:tts`，失败回退文字气泡），聊天气泡式输入 + 预设标签；前进/返回/跳过，仅名字必填。silhouette 的 `clarity` 随答题进度上升（变清晰）。
- **孵化**：silhouette 旋转 + 光晕（无百分比），并行 `POST /api/companion/avatar` 生 portrait（最多重试 3 次；失败以 silhouette 顶替，不阻断）。
- **portrait 确认**：确认 / 重新生成（真实 re-POST）/ 音色确认（mock 候选 + 试听，Backend 暂无 voice matching）。
- **完成**：调真实 `PUT /api/companion/persona` 落库（见 Mock 边界的 persona 约束）→ TTS 念第一句问候 → `onCompleted` → lifecycle `ready`。
- onboarding 全程 `setIgnoreMouseEvents(false)`（输入可用），卸载恢复 click-through。

## 桌面交互契约（`SpriteStage`）

精灵窗口铺满工作区、默认点击穿透。交互解析全部在 `SpriteStage` 的 pointer 层，子组件（蛋等）纯视觉、无自身事件：

- **点击穿透捕获/释放**：`forward:true` 让 mouse-move 穿透时仍转发；渲染层对 mouse-move hit-test 形象 rect，进入 → `sprite.setIgnoreMouseEvents({ignore:false})` 捕获，离开 → `({ignore:true,forward:true})` 释放。捕获态下元素才收到 click 等。
- **拖拽 vs 戳 vs 双击**：pointer-down 记起点；移动超阈值 = 拖拽（改窗内坐标 + 松手 `sprite.setPosition` 持久化休息位）；未移动的 pointer-up = 戳（单击）；320ms 内第二次 = 双击（唤起 chat，Slice 3）。三者在同一 pointer 流上靠移动量 + 时序区分，子组件无 onClick。

## Mock 边界（Desktop-first）

Backend 伙伴能力多数尚未实现（见 [plan.md §5](../../../plan.md) 依赖表）。Desktop 侧在缺口处走 `backend-companion-mock.ts`（dev flag 开关，生产构建剔除），生产实现由 Backend 独立交付。MVP Slice 1 不触达这些（蛋 + auth 同步不依赖 Backend）；Slice 2 起接入，关键约束：**onboarding 完成时必须调真实 `PUT /api/companion/persona` 落库**（`persona_service` 仅 `is_complete=true` 才注入 chat system prompt），否则后续 chat 面对无人格伙伴。
