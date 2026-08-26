# Companion 状态机与表情契约

> Client 伙伴层的运行时契约。`ARCHITECTURE.md` 锁定跨模块设计意图，本文件记录 Client 侧独有的状态机优先级、表情覆盖、过渡语义与不能从代码结构直接读出的边界。

## 1. 动画状态机（8 态）

| 状态 | 优先级 | 触发源 | 持续 |
|---|---|---|---|
| `disconnected` | 100 | Backend WS 断连 | 持续；恢复需 WS 重连 |
| `interacting` | 80 | 用户戳 / 拖 / 悬停 | 瞬态 1.5–2.0s，回到 `previousState` |
| `working` | 70 | 用户活动 ≥ 6 次/10s | 持续；10s 无活动 `force: true` 回 `idle` |
| `speaking` | 60 | TTS 播放 | 与 TTS 音频等长 |
| `thinking` | 50 | LLM 流式响应开始 | 持续至 `message.complete` |
| `listening` | 40 | 用户开始输入 | 持续至用户停止输入或后端响应 |
| `emotional` | 35 | `affect` cue 到达 | 瞬态 2.5s，回到 `previousState`（**叠加非抢占**） |
| `idle` | 10 | 默认 | 持续；10–25s 随机切 IDLE 变体 |

### 1.1 状态切换规则

- **低优先级不能打断高优先级**（`setSpriteState(name)` 默认 `force: false`）—— 已在 `working` 时调用 `setSpriteState('idle')` 被门控逻辑直接吞掉。需强制回退必须传 `{ force: true }`（working 状态 10s 无活动自动 force 回 idle）。
- **`emotional` / `interacting` 是叠加而非抢占**：进入前若当前不是这两个状态，原子 `$previousState` 记录原态；瞬态 timer 结束后回到 `previousState`（若 prev 也是 emotional/interacting，则回 `idle`）。
- **crossfade ~250ms**：clip 切换通过 sprite-stage 的 fade 层处理，避免硬切。

### 1.2 EMOTIONAL 帧时机（ARCH §6.3）

对话完成帧内联情绪字段。当情绪存在且 ≠ `neutral`：

1. 立即 `setSpriteState('emotional', { emotion })`
2. 若 `responseMode === 'voice'`，**延迟 1.2s** 后再 `setSpriteState('speaking')` + `speak()` —— 让 EMOTIONAL 帧可见
3. 若 `responseMode === 'text'`，直接进入下一句渲染，不强制 speaking 状态

`emotion === 'neutral'` 不触发 EMOTIONAL 状态，直接回 `idle`。这是 LLM 的"无特定情绪"答案，不是"中性情绪"。

## 2. 情绪表达（表情头像 + 肢体动画）

情绪枚举 21 项内置（不含 `neutral`；后端权威 `BUILTIN_EMOTIONS` 22 项含 neutral）∪ 自创情绪注册表（`create_expression` 落库、经 `/expressions` 水合）。`neutral` 不触发 EMOTIONAL，直接回 `idle`；LLM 任何未注册 token 走 `affect.py` 的 neutral 回退，tag 剥离后归 `idle`。

情绪的渲染分工（3D 面部不承载情绪表情——生成模型脸部在桌面尺寸下精细度不足）：

- **表情头像**：情绪激活时 chat-dock 左栏头像换为对应表情图（`POST /api/companion/expression-avatar` 按情绪 token 后端 match-or-generate）。store 按情绪 token 缓存结果（token 与服务端缓存行 1:1，单缓存即可）、失败 60s backoff；**生成结果永不浪费**——只要情绪未变，无论多晚生成完就换入；即便错过本次（情绪已切换）也同时落库落缓存，下次同情绪即时命中；情绪结束 / 未就绪 / 失败显示一律回退 portrait。订阅挂在 ChatDock 组件内——聊天窗关闭即不请求，桌面-only 情绪不触发生成。avatar 重生事件清全部缓存（身份锚点变了）。
- **肢体动画**：按模型映射解析当前状态可用动画；解析与兜底规则见 [docs/PIPELINE.md §5](../../../docs/PIPELINE.md)。自创情绪不携带专属肢体动画，复用现有动画。情绪胶囊显示内置情绪与注册表情绪的并集，未水合标记兜底渲染。

## 3. 三档打扰（Client 实现）

档位产品规则与生效条件见 [DESIGN.md §6.2](../../../DESIGN.md)。Renderer 只消费生效档位：用户偏好保存在伴生设置，活动感知器写入覆盖值，空间策略、主动消息呈现与 TTS 门控读取同一生效值；后端拒绝写入时回滚本地偏好并记录开发日志。

## 4. 3D 渲染资源降级与功耗调度

渲染栈是 `three/webgpu` 的 **WebGPURenderer + 四层回退**：WebGPU 后端 → three 内置 WebGL2 后端（同 API 面，零代码）→ 经典 `WebGLRenderer`（仅当 `init()` 整体 reject；必须换新 canvas——webgpu 上下文成功过的 canvas 要不到 webgl2）→ `EngineInitError`（程序化蛋形兜底）。`Engine.create()` 是异步工厂，canvas 由 Engine 自建自管（React 只渲染容器），companion-3d 的 load effects 一律 await 引擎就绪 Promise；实际后端写 dev log。

视觉兜底层级见 [DESIGN.md §1.2](../../../DESIGN.md)。本节只记录 3D 引擎加载行为：模型字节到达并完成解析后才视作可渲染（避免把"模型就绪事件早于字节落地"误当成可渲染状态）；GLB 解析失败回退到程序化蛋兜底，3D 引擎 init 失败亦同。

**渲染功耗三档**（[3d/PowerProfile.ts](3d/PowerProfile.ts) 判定 + [3d/power-signals.ts](3d/power-signals.ts) 订阅，Engine 自门控循环执行）：主进程为后台流式聊天全局禁用了 Chromium 节流，浏览器不会替 7x24 常驻的精灵窗降频，所以循环在 Engine 内按信号自门控——active 60fps（speaking/thinking/listening/working/emotional/interacting）、idle 30fps（idle/disconnected）、dormant 4fps（`$screenLocked`、`document.hidden`、`$focusContext.fullscreen`）。信号全部来自既有渲染端 atom，功耗调度是纯 Client 内部决策（ARCH §7 语义/渲染解耦），无协议与主进程参与。

两条防坑约束：**Ready 保护**——首个模型 `loadCharacter` 落定（`$modelLoadSettled`）前强制 active，避免孵化动画被误降频拉长；**隐藏窗口降级**——Chromium 对 hidden 窗口硬停 rAF（禁节流开关管不到合成层），active/idle 档在 `document.hidden` 时改由 16/37ms timer 驱动，`visibilitychange` 恢复 rAF。dormant 恒为 250ms timer（进程级禁 timer 节流，锁屏下稳定）；档位回升时 Engine 层把 delta 钳制到 50ms，防 mixer 在长暂停后跳变。

**性能取舍**（与上述 3D 渲染叠加生效）：阴影默认关闭（300×360 精灵窗的 PBR 环境光足以体现深度，2048² PCFSoft 阴影是单项最大 GPU 成本），开启时强制 1024² PCF；MSAA 开启（4×——精灵悬浮在任意桌面内容之上，剪影锯齿是首要画质破绽，此尺寸下 resolve 成本可忽略）、贴图预乘 alpha 关闭、DPR 封顶 1.5——小透明窗口的其余 GUI 成本剔除。GLB 解析模板走 [gltf-instance-cache.ts](3d/gltf-instance-cache.ts)：按 `contentHash` 缓存解析后的 scene + animations，切换模型时走 `SkeletonUtils.clone` 深克隆重建骨骼与蒙皮绑定并隔离 AnimationClip 状态；模板持有 GPU 资源并通过引用计数管理生命周期，实例卸载不释放共享资源；支持 LRU 淘汰并在登出时安全释放。OPFS 字节缓存（[glb-opfs-cache.ts](3d/glb-opfs-cache.ts)）以 `contentHash` 为键（同源私有文件系统，而不是 `caches.open` 的 HTTP 缓存），第二次加载走 0 ms 落盘读。Renderer 错误隔离（`$engineError`）：Engine tick 抛错时停止 ticker 并上报，避免"每帧抛+日志洪水"循环。这些都是默认关闭或收紧后的基线，需要在 Settings 重新打开的功能必须经过实测。

## 5. 屏锁与端忙

- `companion/activity.ts` 每 30s 调 `system.is_screen_locked`（`runnerInvoke`）。结果写入 `$screenLocked` atom。
- `$screenLocked.get() === true` 抑制主动消息文本与语音；情绪通道不受锁屏拦截（DESIGN §6.2），affect 照常切 EMOTIONAL。静止档不在此列——主动情绪在源头已断流，客户端对 affect 事件做防御性跳过。
- 屏锁恢复后静默恢复；断连降级（disconnected）曾被表达过时，重连后由 boot 层用确认音色补一句"回神"台词（内容寻址缓存，同 (音色, 台词) 只花一次额度）。

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

- **命中模型**：精灵区域在矩形命中后做像素级精化——3D 模式由 [3d/silhouette-hit.ts](3d/silhouette-hit.ts) 驱动引擎剪影探测——`Engine` 把场景渲进 1/4 分辨率离屏 RT（clear alpha 0，只有实际绘制的像素计入，天然含当前姿态）异步读回 alpha，window 级 mousemove（穿透态下 pointer 事件到不了 canvas）rAF 合并请求、250ms TTL 让并发请求共享一次刷新，答案落地后手动触发 capture probe 处理静止光标；2D 模式由 puppet 链判定（CPU 轻量，PROTOCOL §1.4）——[PuppetStage 六区](puppet/PuppetStage.tsx)（rig 锚点/层矩形 → `$mesh2dHitmap` 总线），hitmap 落地/清空时同样触发 capture probe。两条路径的就绪信号落地前（boot/加载空挡）回退精灵矩形，之后未命中点严格判否——扫过矩形空白区不捕获。精灵矩形不外扩 padding——CSS 光晕是装饰而非命中可供性，点击可见光晕不触发交互。capture 必须在 mousemove 阶段判定成功：`setIgnoreMouseEvents({ forward: true })` 不转发 mousedown，窗口必须在 mousedown 到达前 un-ignore。
- **戳**（`onTap`）：走 LLM 推理（受设置开关与 5 分钟频控门限控制）或从 [reactions/manifest.json](reactions/manifest.json) 预制台词池中按 (bucket, tone) 挑选；云端推理接受 `poke` / `pet` / `dizzy` 三类语义 kind（PROTOCOL §1.4），摸头与眩晕与戳同走该通道（手势识别器 [sprite/gesture-tracker.ts](sprite/gesture-tracker.ts)：head/face 横向往复 = 摸头、狂甩 = 眩晕）；
- **拖拽**（`onDragEnd`）：纯本地预制反馈（零 RPC），从 `manifest.json` 的 drag 桶（性格 + 通用分组）随机挑选。
- **预制反馈 TTS 缓存**：预制台词由 `speakScripted`（[tts.ts](tts.ts)）→ `spiritagent:media:tts { persist: true }` 合成并按 `sha1(音色 + 台词)` 内容寻址缓存在 `$SPIRITAGENT_HOME/audio/tts-cache/<lang>/`：首次播放合成一次并落盘，之后都是本地读盘，同一组 (音色, 台词) 一辈子只花一次云端额度。换音色或改台词会让缓存键变化从而自然失效，没有需要维护的失效逻辑；只有云端结果落盘，Piper 兜底产物不写，否则它会冒充用户选定的云端音色。音色试听句走同一条路径。
- **悬停**：视线跟随光标（2D/3D 同规则）；2D 模式命中头发/裙摆区域额外触发 jiggle 物理抖动（200ms 节流）。贴边吸附态下悬停滑出要求部件级命中——穿透转发的 mousemove 在矩形空白区不触发。情绪 / 交互粒子反馈（爱心、怒气、冷汗、眩晕星环、音符、睡眠气泡）由 [vfx.tsx](vfx.tsx) 挂载在 SpriteStage 上层。
- **右键**：托盘菜单入口（声音切换、伙伴设置、登出）。精灵窗口内右键开自定义 in-sprite 菜单（[sprite/context-menu.tsx](sprite/context-menu.tsx)）——始终挂载、通过 `visibility: hidden` 切换，避免 mount/unmount DOM；状态走 `$contextMenuPos` 原子（[sprite/context-menu-store.ts](sprite/context-menu-store.ts)），菜单自身订阅，宿主 `CompanionRoot` 不参与。菜单可见时注册全屏交互区域与透明 backdrop，点击外部区域、窗口失焦或按下 Escape 键时自动关闭菜单并拦截事件，避免误触精灵拖拽或戳动；若在菜单开启时右键精灵身体部位则直接重定位菜单。
- **语音通话（服务端实时会话）**：客户端不再本地编排转写与朗读——本地只保留 VAD（能量阈值起说 / 静默 1.3s 断句 / 更高阈值打断）驱动 utterance 起止与插话。上行 PCM 经专用 16kHz AudioContext 采集（[pcm-capture.ts](pcm-capture.ts)：AudioWorklet 优先、300ms 预滚保住发音起始瞬态，加载失败降级 ScriptProcessorNode）直发语音 WS；下行音频段由 [segment-player.ts](segment-player.ts) 按到达顺序解码、AudioBufferSourceNode 前瞻调度无缝衔接播放，输出经分析节点驱动与聊天朗读共用的口型振幅汇（两条播放路径互斥）；[voice-session.ts](voice-session.ts) 管连接（现铸 ticket）、掉线重连与控制帧。语音回合事件**不经聊天 WS**（[events.ts](events.ts) 不消费语音事件），但用户话语与精灵回复镜像进聊天 store，保持对话窗实时同步与历史水合一致；字幕内嵌通话面板（[subtitles-overlay.tsx](subtitles-overlay.tsx)，流式文本自动滚到最新一句，关闭或无消息时以占位条保持面板高度稳定）；任一环节失败直接上通话面板错误条，不依赖字幕开关。协议与顺序不变量见 [PROTOCOL.md §1.7](../../../PROTOCOL.md)。

**每日互动统计**：戳击 / 对话轮次两类互动经互动统计上报接口（无 LLM）上报，后端按 UTC 自然日聚合 + OR 门限（任一类 ≥ 10）按日 upsert 一条统计记忆（含小时分布快照），喂给后续 LLM "用户当日活跃度 + 高峰时段" 信号。

## 8. cron 主动陪伴链路

后端自主 turn 入口（`services/scheduler/cron.py::_kick_autonomous_turn`）实现完整链路：

1. Cron CAS 赢得本 tick，直接启动自主回合 task（无 WSEvent 中转）
2. 任务用用户最后 session + JsonRpcEmitter + 用户 dispatcher
3. LLM 可调主动消息工具（可携带情绪）产出伙伴消息事件
4. Client 按 §3 三档规则消费

用户离线（无 dispatcher）→ 任务静默跳过；`next_run_at` 已被 CAS 推进，下一个调度周期重新到期后再尝试。

## 9. 不能从代码结构直接读出的边界

- **3 种 token 通过守卫**：
  - `STT` 数据 > 25 MiB → runner 端拒绝（`audio_io.DEFAULT_MAX_INPUT_BYTES`）
  - `TTS` 文本 > 4000 字符 → 拒绝
  - `runner:invoke` 60 次/秒 token bucket
- **Stop 按钮双通道**：`session.interrupt`（停 LLM 流）+ `runnerCancel`（置 Runner 全局中断标记，让在跑的本地工具尽早退出）；两者均为 best-effort，本地 finalize 兜底 UX
- **持久化键**：伙伴偏好（音色 / 响应模式 / 打扰档位 / 智能反应三开关 / 字幕 / 默认缩放）与各面板位置尺寸均经 localStorage 跨重启保留；`voiceId` 在 ready 后由 [voice-validity.ts](voice-validity.ts) 对云端目录校验（供应商裁剪 / 换源时提示重选，不硬性拒绝）。精灵位置持久化在 `companion-position.json`（Electron userData 目录，非 localStorage）。
- **角色编辑双路径**：`PersonaSection`（表单式直接改 6 个字段）+ `PersonaRetune`（[persona-retune.tsx](persona-retune.tsx) 5–6 步对话式 wizard 含 user_*），后者单 PUT 收尾、保留 `is_complete=True`，且不改写说话风格（说话风格在孵化时定稿，后续修改走设置里的角色管理）。仅 `PersonaSection` 保存后会接入两步形象再生成（先头像 → 用户确认 → 全身）；`PersonaRetune` 是纯 persona 维度调整，不重跑形象流水线。Onboarding 自身始终走两步 UI。
- **形象生成入口分工**：头像重生与全身生成分别走协议定义的独立入口；Renderer 只消费引导状态与生成事件，不组装供应商请求。接口契约见 [PROTOCOL.md §1.2](../../../PROTOCOL.md)，用户流程见 [DESIGN.md §5](../../../DESIGN.md)。引导模式未知时显示加载占位，避免先以错误文案渲染再闪烁。
- **换装（衣柜）**：外观生成 / 穿着 / 删除走 REST（[wardrobe-store](wardrobe/wardrobe-store.ts)）；衣柜入口只在 2D 渲染模式下渲染（3D 模型不随服装变）；换装状态事件触发衣柜重拉，穿着翻转时重水合 2D 渲染层（按新 PSD 重建 puppet），换装期间旧装不断档。
- **签名资产消费**：签名、时效与校验规则见 [PROTOCOL.md §1.5](../../../PROTOCOL.md)；Renderer 只按返回 URL 拉取并缓存。
- **CORS / 跨窗口**：精灵窗口与对话面板共享同一 Electron 渲染进程（panel 是 React child of sprite window）。任何弹层（chat / 语音通话 / 设置）都**不**开关窗口置顶——z-order 恒置顶是 DESIGN §3.7 不变量，通话/设置期间关掉置顶会让精灵连同面板一起沉到别的窗口底下（恢复时还用 `floating` 档，macOS 的 `screen-saver` 档会被降级）。
- **对话内媒体展示（气泡轻量化原则）**：精灵气泡只承载轻量文本；伙伴生成的图片/视频统一在对话窗以媒体卡内联预览、点击放大播放（图片与视频同一交互，[chat-media-card](chat-media-card.tsx) + [media-viewer-overlay](media-viewer-overlay.tsx)）。媒体经主进程 IPC 取回（图片 data URL 走 `apiAsset`、视频字节转 blob URL 卸载回收），不直连后端 URL。聊天窗收起时收到媒体，精灵气泡只提示「点击查看」，点击打开对话窗（媒体属于其他会话时先切过去）；正看其他会话时由通知 toast 承载跳转。后台视频完成的送达行与历史水合同形状（协议见 [PROTOCOL.md §1.3](../../../PROTOCOL.md)）。

## 10. 空间行为（位置 × 移动 × 缩放）

设计意图见 [DESIGN.md §3](../../../DESIGN.md)。本节记录 Client 侧的实现契约。

**单一权威源**：[spatial.ts](spatial.ts) 拥有所有空间状态——`$spatialPos`、`$spatialScale`、`$spatialLocale`、`$spatialLocomotion`。sprite-stage.tsx 是纯消费者（`useStore` + 事件转发到 spatial 函数），只消费位置状态。

**移动引擎**：3D 模式下采用 rAF 插值（非 CSS transition），walk ≈ 80 px/s、fly ≈ 400 px/s。用户拖拽瞬时覆盖一切其他移动。任何新 `moveTo` 或 drag 自动取消正在进行的动画。

**`initSpatial()`**：在 root.tsx mount 时调用一次，注册所有空间反应——$chatOpen（打开对话时终止移动保持就地、精灵自动隐藏，关闭时在原位恢复）、$spriteState（自适应缩放）、$effectiveTier（空间策略 + 缩放）、$focusContext（perch 决策）。返回 cleanup 函数。

**决策树**（`updateSpatialDecision`）：drag > chat / voice call（冻结空间决策，精灵留原位）> still → home > 非 autonomous（常规）→ 停留原地，仅停掉进行中的漫游 > 智能驱动开 → LLM 决策（autonomy.ts 仅在自主档咨询云端）> 焦点窗口几何可用 + category ∉ {unknown, gaming} + !fullscreen → perch > idle + 桌面空闲 + 无 perch 目标 → roam > home。每次 tier / focus / state 变化触发重评估。「沉浸式 → 静止」的档位覆盖只把 gaming / 全屏算作沉浸上下文——专注工作不压档（DESIGN §6.2）。

**语音通话面板刚体绑定**：通话面板的位置完全由精灵位置派生——恒锚在精灵脚下水平居中，用户拖面板时位移直接写进精灵位置（释放复用精灵本体的落点结算：抛掷自由落体会落在面板上，因为通话中"地面"抬高到面板上沿），因此面板与精灵永远保持相对位置不变。**跨文件不变量**：面板尺寸常量（[spatial.ts](spatial.ts)）必须与面板实际渲染尺寸（[voice-call-dock.tsx](voice-call-dock.tsx)）一致——锚定、上提量与拖拽钳制都按它计算，改尺寸必须两处同步。开启通话时脚下放不下面板则平滑上提精灵让位，挂断后回落原位；用户在通话中拖动过精灵或面板即接管位置，回落作废。通话中精灵 y 上限收紧到"脚下放得下面板"，贴边探头吸附与仪式行走（Ritual walk）被抑制，窗口 resize 只按新视口收紧当前位置、不重贴 home。

**perch 位置**：从焦点窗口几何（`$focusContext.windowGeom`）计算——优先窗口右下角外侧，右溢出则尝试左侧；两侧放不下全尺寸时等比例缩到能舒适栖身（不低于 0.5×，缩放上限随 perch 场所生效、离开即解除，压过情绪放大）。连最小尺寸都容不下才放弃。perch 仅在 idle 时发起；进入 perch 后 work/think/speak 状态不踢出（"陪"语义）。

**roam**：自补充式 waypoint 循环（每个点停 5–15s），waypoint 在屏幕下半部随机生成。自主档 + idle + 桌面空闲（Runner 上报的空闲秒数 ≥ 90s，未知信号保守不漫游）+ 无 perch 目标时触发（2D/3D 均漫游；2D 走躯干复合步态，见 2D 渲染层 README）。任何 drag / chat / focus / tier 变化或用户回到桌面通过 `stopRoam` 终止。

**缩放**：`$defaultScale`（用户设置，localStorage）是基准。EMOTIONAL 状态的 excited/surprised/playful 触发 1.3–1.6× 临时放大，静止档不放大。缩放也是 rAF 动画（~300ms），通过容器 `transform: scale()` 实现——与 sprite 内部的程序化动画（呼吸/浮动）在不同 DOM 层，不冲突。

**Backend 零感知**：所有空间决策在 Client 本地完成，无 WS 事件或 RPC 新增。Runner 提供感知能力（`system.get_windows` 窗口枚举、`system.get_focused_app` 焦点窗口几何）但 Runner 也不知道空间行为存在。

**Ritual walk**（[ritual-walk.ts](ritual-walk.ts)）：交互类工具（`system.open_application` / `browser_*` / `system.click_at`）在 events.ts 的 tool.call 分发里拦截。目标解析按工具分派：`click_at` 的目标就是点击坐标本身（包成虚拟窗口几何，**execute 即那次点击，不再额外补一次 click**——否则双击）；其余工具从 args（name/url/keyword）提取关键词经 `system.get_windows` 匹配既有窗口，关键词为空不进入仪式（空串会让 `includes` 恒真、匹配到第一个无关窗口）。找到目标后：途中视线锁定目标中心（`$gazeTarget` 显式覆盖指针跟随，2D/3D 同规则）→ 远距离（>400px）fly、近距离 walk 到目标旁 → 抵达后按方位播 `point_left/right` 再接 `click`（open_application 等先在目标中心补一次聚焦点击，click_at 跳过）→ INTERACTING 1.5s → execute 原工具 → 返回原 locale。找不到目标窗口或无处栖身时以一句人格化台词表达（走 speakProactive 的档位门控）后静默走常规工具调用兜底；gaze 的清除走 try/finally，异常路径不泄漏。chat 开启或屏锁时直接执行不走路。
