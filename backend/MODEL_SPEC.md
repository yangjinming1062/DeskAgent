# 伙伴 3D 模型规格

> 伙伴 3D 模型的骨骼、morph target、动画与材质规格。**这是 3D 生成管线与引擎开发者之间的唯一契约**——引擎按本规格的命名做动画回退与 morph 解析，生成管线按本规格产出模型。

## 生成管线

伙伴 3D 模型由 Backend 单一路径生成：

1. 以 seed 全身种子图（onboarding 产出的配对形象图）为唯一输入，调 Tripo3D image-to-model 生成几何 + PBR 纹理；
2. rig-check 确认骨骼结构后以 `rig(spec=mixamo)` 绑定 25 关节 Mixamo Humanoid 骨骼；
3. 下载 rigged GLB（模型 URL 5 分钟过期，须立即下载），由 Blender headless 注入 ~50 个 morph target（Tripo3D 不生成 blendshape）；
4. 保存并经 `model.ready` 事件下发；任一步失败 emit `model.failed`，客户端渲染程序化蛋形兜底角色。

动画不内嵌 GLB——全部动画 clip 由客户端 TypeScript 骨骼旋转关键帧（`client/renderer/companion/3d/companion-clips.ts`）在加载时注入，零运行时生成成本。

## 覆盖约束

引擎按可用性回退：缺失的动画静默降级到 `idle`，缺失的情绪 morph 静默降级为中性面部。模型必须完整覆盖：

- **交互状态机（9 态）**：IDLE / LISTENING / THINKING / SPEAKING / WORKING / EMOTIONAL / SLEEPING / INTERACTING / DISCONNECTED。每个状态至少一个对应动画 clip；状态切换由四类触发源驱动——规则（app 启动/退出、工具调用、用户输入起止、TTS 播放起止、时间）、LLM affect（Backend 下发的 `affect: {emotion}` cue）、用户直接操作（戳/拖/悬停）、自主行为（IDLE 下随机微动作）。
- **情绪（16 项）**：`ALLOWED_EMOTIONS`（happy / sad / surprised / excited / confused / concerned / shy / proud / grateful / playful / bored / lonely / sleepy / curious / embarrassed / apologetic；`neutral` 不触发情绪表达）。每个情绪至少能被 morph target 组合表达，配套动画从 §3.7 / §3.8 / §3.13 的情绪 clip 中选取。
- **空间移动**：walk ≈ 60–100 px/s（闲适慢走）、fly ≈ 300–500 px/s（赶路）、drag 瞬时（用户操作不能有延迟）。
- **用户直接交互**：单击戳（高频戳触发递进反应，按性格分层：粘人型撒娇 / 毒舌型吐槽 / 管家型礼貌）、双击唤起对话、长按 / 拖拽（松手回弹）、悬停（注意到鼠标）。
- **自主行为**：IDLE 下 10–25s 随机间隔插入微动作（纯视觉，不触发 TTS、不弹气泡）；情境 idle 按焦点应用分类（ide / music / reader / gaming / browsing / other / unknown）切换。

## 设计原则

- **完整覆盖交互状态机**：每个状态至少有一个对应动画 clip；每个情绪至少能被 morph target 组合表达。
- **ARKit BlendShape 标准**：面部 morph target 采用 ARKit 52 BlendShape 命名（跨行业通用、Three.js / Blender / Unity 无缝识别），不使用 VRM 或 Daz 专有命名。
- **Mixamo Humanoid 骨骼**：25 关节标准 humanoid rig（Hips → Head + 四肢），Mixamo / Unreal / Unity 原生兼容，保证第三方动捕资产可直接 retarget。
- **最小必选 + 可选扩展**：每个模型必须包含核心动画集（MUST），可选包含微动作 / 情境变体 / 交互反应（SHOULD）。引擎按可用性回退，缺失的动画静默降级到 idle。

---

## 1. 文件格式

| 约束 | 值 |
|------|----|
| 格式 | GLB 2.0（二进制 glTF，单文件） |
| 动画 | 不内嵌——客户端 TypeScript 骨骼旋转关键帧注入，~85 个 clip，命名见 §3 |
| morph 目标名 | `meshes[].extras.targetNames`（Blender glTF 导出器标准位置） |
| 骨骼 | 1 个 `skins[]`，25 关节 Mixamo Humanoid |
| 纹理 | Tripo3D 生成 PBR（albedo/normal/roughness/metalness），内嵌（GLB 单文件） |
| 大小上限 | 10 MB / 文件（加载性能基线） |
| Y 轴朝向 | 模型面朝 -Z（glTF 标准），头朝 +Y |

---

## 2. 骨骼规格（MUST）

Mixamo Humanoid 25 关节，名称严格匹配（引擎按名称查找骨骼做 look-at、spring bone 等）：

```
Hips
├── LeftUpLeg → LeftLeg → LeftFoot → LeftToeBase
├── RightUpLeg → RightLeg → RightFoot → RightToeBase
└── Spine → Spine1 → Spine2
    ├── LeftShoulder → LeftArm → LeftForeArm → LeftHand
    ├── RightShoulder → RightArm → RightForeArm → RightHand
    └── Neck → Head
                ├── Jaw
                ├── LeftEye
                └── RightEye
```

`Head`、`LeftEye`、`RightEye` 必须存在——引擎的 look-at 功能依赖这三个关节做头部/眼球追踪。`Jaw` 用于嘴部辅助旋转（当 morph target 不可用时降级）。

---

## 3. 动画 clip 目录（TypeScript 骨骼旋转关键帧）

### 定义方式

每个 clip 是 `companion-clips.ts` 中一个返回 `THREE.AnimationClip` 的函数，播放时按 Mixamo bone 名匹配注入 mixer。

- **坐标系**：Y 轴朝上，模型面朝 -Z，T-pose 为初始姿态。
- **旋转**：欧拉角 [x, y, z] 弧度。范围参考：手臂 ±1.5rad，脊柱 ±0.5rad，头部 ±0.3rad。
- **命名约定**：小写蛇形（`idle`、`idle_look_around`、`poke_light`）。
- **循环 clip**：首尾帧无缝衔接，时长 3–6s。
- **单次 clip**：播放一次后回到 idle（引擎自动 crossFade ~250ms），时长 0.5–2.5s。

### 3.1 核心状态动画（MUST — 9 个）

引擎经 `AnimationMap` 按状态名查找：

| clip 名 | 类型 | 时长 | 状态 | 动作描述 |
|---------|------|------|------|----------|
| `idle` | 循环 | 4–6s | IDLE | 自然呼吸：胸腔起伏、重心微移、偶发眨眼（morph 驱动）。最核心的动画，用户看到的 80% 时间是它 |
| `listening` | 循环 | 3–4s | LISTENING | 头微倾向用户、目光聚焦、身体前倾——"我在听" |
| `thinking` | 循环 | 3–5s | THINKING | 手托下巴或手指点额、目光上移、偶尔点头——"让我想想" |
| `speaking` | 循环 | 3–5s | SPEAKING | 对话手势：双手交替比划、自然停顿。嘴型由 `jawOpen` morph 按 TTS 振幅实时驱动，动画本身不含嘴部关键帧 |
| `working` | 循环 | 3–5s | WORKING | 前倾专注、双手模拟打字/操作。节奏比 speaking 快、比 thinking 稳 |
| `sleeping` | 循环 | 5–8s | SLEEPING | 眼闭、头垂、呼吸深缓、身体微摇。最慢的循环，节奏明显慢于 idle |
| `interacting` | 单次 | 1–2s | INTERACTING | 被戳后的反应起始——轻微弹跳 + 回头看向用户。后续 poke_* 变体在此基础上升级 |
| `emotional_idle` | 循环 | 3–4s | EMOTIONAL | 情绪保持期的中性躯干循环（情绪细节由 morph 驱动面部）。大多数情绪不需独立骨骼动画——morph 表情叠加 emotional_idle 躯干即可 |
| `disconnected` | 循环 | 4–6s | DISCONNECTED | 打哈欠、歪头、目光涣散——犯困/走神。比 sleeping 轻、比 idle 涣散 |

### 3.2 微动作变体（SHOULD — 6 个）

IDLE 时 10–25s 间隔随机插入。引擎按名称回退到 `idle`：

| clip 名 | 类型 | 时长 | 动作描述 |
|---------|------|------|----------|
| `idle_look_around` | 单次 | 2–3s | 左右张望、看上下——好奇/无聊张望 |
| `idle_blink` | 单次 | 0.5s | 用力眨眼/揉眼（配合 morph 的自动眨眼，这是"额外的"重眨眼） |
| `idle_stretch` | 单次 | 2–3s | 伸懒腰、扩胸——解压动作 |
| `idle_shift_weight` | 单次 | 1–2s | 重心从一脚换到另一脚、活动肩颈 |
| `idle_yawn` | 单次 | 2–3s | 打哈欠 |
| `idle_fidget` | 单次 | 1–2s | 小动作坐立不安、摆弄衣角/手指 |

### 3.3 情境 idle 变体（SHOULD — 6 个）

检测到焦点应用分类（ide / music / reader / gaming / browsing / other / unknown）后切换。引擎按名称回退到 `idle`：

| clip 名 | 适用焦点分类 | 动作描述 |
|---------|-------------|----------|
| `idle_humming` | 通用 | 放松哼歌、身体微晃 |
| `idle_dreamy` | 通用 | 目光放空、沉浸幻想 |
| `idle_typing` | IDE | 手指在空中模拟打字、节奏慢于 working |
| `idle_bounce` | Music | 跟着节拍微微弹跳、头点拍 |
| `idle_calm` | Reader | 极安静的呼吸、几乎不动、偶尔翻页方向看 |
| `idle_engaged` | Gaming | 前倾、手握持状、专注 |

### 3.4 移动动画（MUST walk / SHOULD 其他 — 5 个）

空间移动：walk ≈ 60–100 px/s、fly ≈ 300–500 px/s、drag 瞬时：

| clip 名 | 类型 | 时长 | 动作描述 |
|---------|------|------|----------|
| `walk` | 循环 | 1–2s | 向前行走循环。步幅自然、与 60–100 px/s 速度匹配 |
| `idle_to_walk` | 单次 | 0.5s | idle → walk 过渡起步 |
| `walk_to_idle` | 单次 | 0.5s | walk → idle 停步减速 |
| `fly` | 循环 | 2–3s | 飞行循环（飞行类物种使用） |
| `drag` | 单次 | 0.5s | 被拖拽时的悬挂姿态——身体下垂、四肢放松 |

### 3.5 互动反应动画（SHOULD — 6 个）

用户直接交互（戳/拖）的反应。引擎按名称回退到 `interacting`：

| clip 名 | 类型 | 时长 | 触发场景 | 动作描述 |
|---------|------|------|----------|----------|
| `poke_light` | 单次 | 0.8s | 轻戳 | 轻微回头 + 疑惑表情 |
| `poke_heavy` | 单次 | 1.2s | 高频戳 | 明显弹跳 + 惊讶/不满 |
| `poke_happy` | 单次 | 1s | 粘人型被戳 | 开心回头 + 笑 |
| `poke_angry` | 单次 | 1s | 易怒型被高频戳 | 回头瞪视、不满 |
| `poke_shy` | 单次 | 1s | 害羞型被戳 | 缩肩低头、躲闪 |
| `drag_end` | 单次 | 0.8s | 拖拽结束松手 | 落地回弹 + 定神 |

### 3.6 仪式性过场动画（SHOULD — 3 个）

孵化完成 / 首次问候与告别等仪式感时刻：

| clip 名 | 类型 | 时长 | 场景 | 动作描述 |
|---------|------|------|------|----------|
| `greeting` | 单次 | 2–3s | 孵化完成 / 首次问候 | 挥手 + 笑——welcome |
| `goodbye` | 单次 | 2s | 告别 | 轻挥手 + 目送 |
| `wake_up` | 单次 | 2–3s | 从 SLEEPING 唤醒 | 揉眼、伸懒腰、回神 |

### 3.7 正面情绪（SHOULD — 8 个）

| clip 名 | 类型 | 时长 | 动作描述 |
|---------|------|------|----------|
| `dance_happy` | 单次 | 2–3s | 快乐小舞步 |
| `celebrate` | 单次 | 1.5–2s | 握拳庆祝、高举双手 |
| `giggle` | 单次 | 1–1.5s | 捂嘴偷笑、肩膀耸动 |
| `cheer` | 单次 | 1–1.5s | 欢呼雀跃、双手挥舞 |
| `clap` | 单次 | 1–1.5s | 拍手鼓掌 |
| `spin_happy` | 单次 | 2s | 原地转圈 |
| `jump_joy` | 单次 | 1s | 开心跳起 |
| `heart_pose` | 单次 | 1.5–2s | 双手比心 |

### 3.8 负面情绪（SHOULD — 8 个）

| clip 名 | 类型 | 时长 | 动作描述 |
|---------|------|------|----------|
| `pout` | 单次 | 1.5s | 嘟嘴赌气 |
| `stomp_angry` | 单次 | 1.5s | 跺脚生气 |
| `sulk` | 循环 | 3–4s | 生闷气、抱臂低头 |
| `cry` | 单次 | 2–3s | 抹泪哭泣 |
| `tremble_fear` | 循环 | 2–3s | 害怕发抖 |
| `collapse_sad` | 单次 | 2s | 失落地垂头/瘫坐 |
| `shake_frustration` | 单次 | 1.5s | 摇头甩手、挫败 |
| `withdrawal` | 循环 | 3–4s | 蜷缩后退、回避 |

### 3.9 社交互动（SHOULD — 6 个）

| clip 名 | 类型 | 时长 | 动作描述 |
|---------|------|------|----------|
| `wave_warm` | 单次 | 1.5–2s | 温暖挥手 |
| `bow` | 单次 | 1.5s | 鞠躬行礼 |
| `fold_arms` | 循环 | 2–3s | 双臂抱胸站立 |
| `standing_relax` | 循环 | 2–3s | 放松站姿、重心微移 |
| `shrug` | 单次 | 1s | 耸肩摊手 |
| `shake_head` | 单次 | 1s | 摇头否定 |

### 3.10 亲密互动（SHOULD — 10 个）

| clip 名 | 类型 | 时长 | 动作描述 |
|---------|------|------|----------|
| `hug_offer` | 单次 | 2s | 张开双臂求抱 |
| `hug_receive` | 单次 | 2s | 被抱住、回搂 |
| `kiss_lips` | 单次 | 1.5s | 亲吻（嘴唇） |
| `kiss_cheek` | 单次 | 1.5s | 亲吻（脸颊） |
| `lap_pillow` | 循环 | 3–4s | 枕膝休息 |
| `lean_on_shoulder` | 循环 | 3–4s | 靠肩依偎 |
| `whisper` | 单次 | 1.5s | 凑近耳边低语 |
| `cuddle` | 循环 | 3–4s | 依偎蹭蹭 |
| `hold_hand` | 循环 | 2–3s | 牵手 |
| `pat_receive` | 单次 | 1.5s | 被摸头、享受眯眼 |

### 3.11 更私密互动（SHOULD — 5 个）

| clip 名 | 类型 | 时长 | 动作描述 |
|---------|------|------|----------|
| `intimate_embrace` | 循环 | 3–4s | 深情相拥 |
| `sleep_together` | 循环 | 4–6s | 依偎入睡 |
| `carry_princess` | 循环 | 3–4s | 公主抱 |
| `forehead_touch` | 单次 | 2s | 额头相抵 |
| `nuzzle` | 单次 | 2s | 蹭脸颊/蹭手心 |

### 3.12 日常活动（SHOULD — 6 个）

| clip 名 | 类型 | 时长 | 动作描述 |
|---------|------|------|----------|
| `sit` | 循环 | 3–4s | 坐下姿态（含坐下过渡） |
| `eat` | 循环 | 2–3s | 吃饭咀嚼 |
| `drink` | 循环 | 2–3s | 喝水/饮品 |
| `read` | 循环 | 3–4s | 看书翻页 |
| `pet_animal` | 循环 | 2–3s | 抚摸小动物 |
| `exercise_stretch` | 循环 | 3–4s | 拉伸运动 |

### 3.13 惊喜反应（SHOULD — 7 个）

| clip 名 | 类型 | 时长 | 动作描述 |
|---------|------|------|----------|
| `surprise_jump` | 单次 | 1s | 吓一跳跳起 |
| `shock_stepback` | 单次 | 1s | 震惊后退一步 |
| `dizzy` | 循环 | 2–3s | 头晕目眩、摇晃 |
| `embarrassed_cover` | 单次 | 1.5s | 捂脸害羞 |
| `proud_pose` | 单次 | 1.5s | 叉腰骄傲 |
| `relieved_sigh` | 单次 | 1.5s | 松口气、拍胸口 |
| `curious_lean` | 单次 | 1.5s | 好奇凑近打量 |

### 3.14 动画总览

| 类别 | 数量 |
|------|------|
| 核心状态 | 9 |
| 微动作 | 6 |
| 情境 idle | 6 |
| 移动 | 5 |
| 互动反应 | 6 |
| 仪式过场 | 3 |
| 正面情绪 | 8 |
| 负面情绪 | 8 |
| 社交互动 | 6 |
| 亲密互动 | 10 |
| 更私密互动 | 5 |
| 日常活动 | 6 |
| 惊喜反应 | 7 |
| **合计** | **85** |

引擎按可用性回退：缺失的 SHOULD 动画静默降级到对应 MUST 动画或 `idle`，用户无感。

---

## 4. Morph Target 规格（~50 个）

Tripo3D 不生成 blendshape——全部 morph target 由 Blender headless 在一次 pass 中注入（`backend/assets/animations/inject_morph_targets.py`），landmark 从 Mixamo 骨骼动态推算，位移按 Head 骨骼长度缩放。下文清单为基础集（44 个），目标规模 ~50——表情需求扩展时在既有组内追加 ARKit 命名。

### 4.1 ARKit 基础表情（MUST — 16 个）

采用 ARKit BlendShape 命名标准，覆盖自动眨眼、TTS 口型同步与基础情绪：

| 组 | 名称 |
|----|------|
| 眼部 | `eyeBlinkLeft`, `eyeBlinkRight`, `eyeWideLeft`, `eyeWideRight`, `eyeSquintLeft`, `eyeSquintRight`, `eyesLookDown` |
| 眉部 | `browInnerUp`, `browInnerDown` |
| 嘴部 | `jawOpen`, `mouthSmile`, `mouthSmileRight`, `mouthFrown` |
| 面颊/鼻/舌 | `cheekSquintLeft`, `noseSneerLeft`, `tongueOut` |

### 4.2 负面/强烈情绪（MUST — 14 个）

| 组 | 名称 |
|----|------|
| 眼部 | `eyeCloseTight`, `eyeDroopLeft`, `eyeDroopRight`, `eyeWidenFear`, `eyeNarrow` |
| 眉部 | `browFurrow`, `browOuterUp` |
| 鼻 | `nostrilFlare` |
| 嘴部 | `mouthTremble`, `mouthCornerDown`, `jawClench`, `lipPress` |
| 面颊 | `faceWince`, `cheekPuff` |

### 4.3 亲密/俏皮（MUST — 8 个）

| 组 | 名称 |
|----|------|
| 眼部 | `eyeCloseLeft`, `eyeCloseRight`, `browRaiseLeft`, `browRaiseRight` |
| 嘴部 | `mouthPucker`, `lipBiteLower` |
| 鼻 | `noseWrinkle` |
| 面颊 | `cheekBlush` |

### 4.4 体型调节（MUST — 6 个）

个性化定制用——onboarding 后由 persona 驱动设入：

| morph 名 | 范围 | 用途 |
|----------|------|------|
| `Body_Height` | 0.0–1.0 | 身高调节 |
| `Body_Weight` | 0.0–1.0 | 体型胖瘦 |
| `Body_Muscle` | 0.0–1.0 | 肌肉/纤细程度 |
| `Body_Shoulders` | 0.0–1.0 | 肩宽 |
| `Face_Width` | 0.0–1.0 | 脸型宽窄 |
| `Face_Jaw` | 0.0–1.0 | 下颌形态 |

### 4.5 Morph Target 不在模型中时的行为

| 类别 | 引擎行为 |
|------|----------|
| `eyeBlink*` 缺失 | 不眨眼（不影响其他动画） |
| `jawOpen` 缺失 | 无 TTS 口型同步（嘴不动） |
| 情绪 morph 缺失 | 该情绪回退为纯 idle 面部（无表情变化但躯干正常） |
| 身体形态 morph 缺失 | 使用模型默认体型 |

---

## 5. 材质与纹理规格

### 5.1 材质槽（MUST）

每个模型至少两个 `MeshStandardMaterial` 槽，支持换装系统的材质覆盖：

| 材质名 | 覆盖范围 | 默认属性 |
|--------|----------|----------|
| `Skin` | 身体 mesh | `roughness: 0.55, metalness: 0.0`，写实肤色 albedo |
| `Eyes` | 眼球 mesh | `roughness: 0.12, metalness: 0.0`，深色虹膜 |

换装系统覆盖优先级：WardrobeItem 的 `material_overrides` 按 mesh 名匹配——通配符 `*` 覆盖所有 mesh，特定 mesh 名精确覆盖。命名一致是换装正确生效的前提。

### 5.2 纹理

Tripo3D 生成的 PBR 纹理（albedo/normal/roughness/metalness）内嵌于 GLB。换装纹理热替是非破坏性的——`CharacterController.setOutfit()` 先 dispose 旧纹理再加载新纹理，不修改骨骼动画或 morph targets。

---

## 6. 物种

物种不绑定预制资产：persona `biological_type` 是自由文本（onboarding 可自由输入或选择快捷标签，空值默认"人类"），全部物种共用同一 Mixamo 骨骼与动画命名——外观差异完全由种子图经 Tripo3D 还原，物种特有动画（如 `fly`）按需从 §3 目录中启用。

---

## 7. 验证清单

模型提交前的检查项：

- GLB 2.0 单文件，< 10 MB
- 25 关节 Mixamo Humanoid 骨骼，关节名严格匹配 §2
- §3.1 的 9 个核心状态动画全部在 `companion-clips.ts` 中定义，命名正确
- §3.4 的 `walk` 动画存在
- 所有循环动画首尾帧无缝
- §4 的 morph targets 全部存在于 Body mesh 的 `extras.targetNames`（Blender 注入后）
- `jawOpen` morph 存在（TTS 口型同步前置条件）
- `eyeBlinkLeft` / `eyeBlinkRight` morph 存在（自动眨眼前置条件）
- 两个材质槽 `Skin` + `Eyes`，命名正确
- 模型面朝 -Z，头朝 +Y
