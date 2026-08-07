# GLB 基底模型规格

> 伙伴 3D 基底模型的动画、morph target、骨骼与材质规格。**这是模型制作者与引擎开发者之间的唯一契约**——引擎按本规格的命名做动画回退与 morph 解析，模型制作者按本规格产出 GLB。

## 设计原则

- **完整覆盖交互状态机**：每个状态（[DESIGN.md §2](../../../../DESIGN.md)）至少有一个对应动画 clip；每个情绪（§2.1 / `ALLOWED_EMOTIONS`）至少能被 morph target 组合表达。
- **ARKit BlendShape 标准**：面部 morph target 采用 ARKit 52 BlendShape 命名（跨行业通用、Three.js / Blender / Unity 无缝识别），不使用 VRM 或 Daz 专有命名。
- **Mixamo Humanoid 骨骼**：25 关节标准 humanoid rig（Hips → Head + 四肢），Mixamo / Unreal / Unity 原生兼容，保证第三方动捕资产可直接 retarget。
- **最小必选 + 可选扩展**：每个模型必须包含核心动画集（MUST），可选包含微动作/情境变体/交互反应（SHOULD）。引擎按可用性回退，缺失的动画静默降级到 idle。

---

## 1. 文件格式

| 约束 | 值 |
|------|----|
| 格式 | GLB 2.0（二进制 glTF，单文件） |
| 动画 | glTF `animations[]`，每个 clip 独立命名 |
| morph 目标名 | `meshes[].extras.targetNames`（Blender glTF 导出器标准位置） |
| 骨骼 | 1 个 `skins[]`，25 关节 Mixamo Humanoid |
| 纹理 | 内嵌（GLB 单文件），PBR albedo/normal/roughness/metalness |
| 大小上限 | 5 MB / 文件（加载性能基线） |
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

## 3. 动画 clip 目录

### 命名约定

- 小写蛇形（`idle`、`idle_look_around`、`poke_reaction`）
- 循环 clip：首尾帧无缝衔接
- 单次 clip：播放一次后回到 idle（引擎自动 crossFade）
- 持续时长：循环 clip 3–6s；单次 clip 0.5–2.5s

### 3.1 核心状态动画（MUST — 9 个）

覆盖 [DESIGN.md §2.1](../../../../DESIGN.md) 全部状态。引擎经 `AnimationMap` 按状态名查找：

| clip 名 | 类型 | 时长 | 状态 | 动作描述 |
|---------|------|------|------|----------|
| `idle` | 循环 | 4–6s | IDLE | 自然呼吸：胸腔起伏、重心微移、偶发眨眼（morph 驱动）。最核心的动画，用户看到的 80% 时间是它 |
| `listening` | 循环 | 3–4s | LISTENING | 头微倾向用户、目光聚焦、身体前倾——"我在听" |
| `thinking` | 循环 | 3–5s | THINKING | 手托下巴或手指点额、目光上移、偶尔点头——"让我想想" |
| `speaking` | 循环 | 3–5s | SPEAKING | 对话手势：双手交替比划、自然停顿。嘴型由 `jawOpen` morph 按 TTS 振幅实时驱动，动画本身不含嘴部关键帧 |
| `working` | 循环 | 3–5s | WORKING | 前倾专注、双手模拟打字/操作。节奏比 speaking 快、比 thinking 稳 |
| `sleeping` | 循环 | 5–8s | SLEEPING | 眼闭、头垂、呼吸深缓、身体微摇。最慢的循环，节奏明显慢于 idle |
| `interacting` | 单次 | 1–2s | INTERACTING | 被戳后的反应起始——轻微弹跳 + 回头看向用户。后续 poke_reaction 变体在此基础上升级 |
| `emotional_idle` | 循环 | 3–4s | EMOTIONAL | 情绪保持期的中性躯干循环（情绪细节由 morph 驱动面部）。大多数情绪不需独立骨骼动画——morph 表情叠加 emotional_idle 躯干即可 |
| `disconnected` | 循环 | 4–6s | DISCONNECTED | 打哈欠、歪头、目光涣散——犯困/走神。比 sleeping 轻、比 idle 涣散 |

### 3.2 微动作变体（SHOULD — 4 个）

[DESIGN.md §5.4](../../../../DESIGN.md) 定义 IDLE 时 10–25s 间隔随机插入的微动作。引擎按名称回退到 `idle`：

| clip 名 | 类型 | 时长 | 动作描述 |
|---------|------|------|----------|
| `idle_look_around` | 单次 | 2–3s | 左右张望、看上下——好奇/无聊张望 |
| `idle_blink` | 单次 | 0.5s | 用力眨眼/揉眼（配合 morph 的自动眨眼，这是"额外的"重眨眼） |
| `idle_stretch` | 单次 | 2–3s | 伸懒腰、扩胸——解压动作 |
| `idle_shift_weight` | 单次 | 1–2s | 重心从一脚换到另一脚、活动肩颈 |

### 3.3 情境化 idle 变体（SHOULD — 6 个）

[DESIGN.md §5.4](../../../../DESIGN.md) 定义检测到 focused app 分类后切换的情境 idle。引擎按名称回退到 `idle`：

| clip 名 | 适用焦点分类 | 动作描述 |
|---------|-------------|----------|
| `idle_thinking` | IDE | 托腮沉思、偶尔看代码方向 |
| `idle_typing` | IDE | 手指在空中模拟打字、节奏慢于 working |
| `idle_bounce` | Music | 跟着节拍微微弹跳、头点拍 |
| `idle_sway` | Music | 身体左右摇摆、放松 |
| `idle_calm` | Reader | 极安静的呼吸、几乎不动、偶尔翻页方向看 |
| `idle_engaged` | Gaming | 前倾、手握持状、专注 |

### 3.4 移动动画（MUST walk / SHOULD 其他 — 5 个）

[DESIGN.md §3.3](../../../../DESIGN.md) 的空间移动：

| clip 名 | 类型 | 时长 | 动作描述 |
|---------|------|------|----------|
| `walk` | 循环 | 1–2s | 向前行走循环。步幅自然、与 §3.3 的 60–100 px/s 速度匹配 |
| `idle_to_walk` | 单次 | 0.5s | idle → walk 过渡起步 |
| `walk_to_idle` | 单次 | 0.5s | walk → idle 停步减速 |
| `fly` | 循环 | 2–3s | 飞行循环（精灵/灵兽/幻形物种 SHOULD；人类/机甲 MAY 用 walk 替代） |
| `drag` | 单次 | 0.5s | 被拖拽时的悬挂姿态——身体下垂、四肢放松 |

### 3.5 交互反应动画（SHOULD — 4 个）

[DESIGN.md §5.3](../../../../DESIGN.md) 定义的用户直接交互反应。引擎按名称回退到 `interacting`：

| clip 名 | 类型 | 时长 | 触发场景 | 动作描述 |
|---------|------|------|----------|----------|
| `poke_reaction_light` | 单次 | 0.8s | 轻戳 | 轻微回头 + 疑惑表情 |
| `poke_reaction_heavy` | 单次 | 1.2s | 高频戳 | 明显弹跳 + 惊讶/不满 |
| `poke_reaction_happy` | 单次 | 1s | 粘人型被戳 | 开心回头 + 笑 |
| `drag_end` | 单次 | 0.8s | 拖拽结束松手 | 落地回弹 + 定神 |

### 3.6 仪式性过场动画（SHOULD — 3 个）

[DESIGN.md §1.1 / §4.6](../../../../DESIGN.md) 的仪式感时刻：

| clip 名 | 类型 | 时长 | 场景 | 动作描述 |
|---------|------|------|------|----------|
| `greeting` | 单次 | 2–3s | 孵化完成 / 首次问候 | 挥手 + 笑——welcome |
| `goodbye` | 单次 | 2s | 告别 | 轻挥手 + 目送 |
| `wake_up` | 单次 | 2–3s | 从 SLEEPING 唤醒 | 揉眼、伸懒腰、回神 |

### 3.7 动画总览

| 类别 | MUST | SHOULD | 总计 |
|------|------|--------|------|
| 核心状态 | 9 | — | 9 |
| 微动作 | — | 4 | 4 |
| 情境 idle | — | 6 | 6 |
| 移动 | 1（walk） | 4 | 5 |
| 交互反应 | — | 4 | 4 |
| 过场 | — | 3 | 3 |
| **合计** | **10** | **21** | **31** |

引擎按可用性回退：缺失的 SHOULD 动画静默降级到对应 MUST 动画或 `idle`，用户无感。

---

## 4. Morph Target 规格

### 4.1 ARKit 52 BlendShape 子集（MUST — 面部表情）

采用 ARKit BlendShape 命名标准。以下 morph targets 覆盖 16 种情绪 + 自动眨眼 + TTS 口型同步：

**眼部（眨眼 + 表情）：**

| morph 名 | 用途 |
|----------|------|
| `eyeBlinkLeft` / `eyeBlinkRight` | 自动眨眼（引擎周期驱动） |
| `eyeSquintLeft` / `eyeSquintRight` | 笑眼/眯眼（happy / playful / embarrassed） |
| `eyeWideLeft` / `eyeWideRight` | 惊讶睁大（surprised / excited） |
| `eyesLookDown` | 眼睑下垂（sad / sleepy / lonely） |

**眉部：**

| morph 名 | 用途 |
|----------|------|
| `browInnerUp` | 扬眉（surprised / excited / curious） |
| `browInnerDown` | 皱眉（sad / concerned / apologetic / confused） |

**嘴部（表情 + 口型）：**

| morph 名 | 用途 |
|----------|------|
| `jawOpen` | 嘴张开——TTS 振幅驱动（lip sync） + 惊讶/困倦 |
| `mouthSmile` / `mouthSmileRight` | 微笑（happy / shy / grateful / proud / playful） |
| `mouthFrown` | 撇嘴（sad / lonely / apologetic） |

**面颊/鼻/舌（情绪细节）：**

| morph 名 | 用途 |
|----------|------|
| `cheekSquintLeft` | 面颊上提（happy / shy / embarrassed） |
| `noseSneerLeft` | 鼻翼皱（playful / disgusted） |
| `tongueOut` | 吐舌（playful） |

> MorphController 经语义名（`blink`、`smile`、`frown` 等）→ ARKit 名/VRM 名/Daz 名别名列表查找实际 morph 索引。模型只要包含本规格的 ARKit 命名即可被正确解析，无需关心引擎内部的别名映射。

### 4.2 身体形态 morph（SHOULD — 6 个）

个性化定制用——onboarding 后由 persona 驱动设入：

| morph 名 | 范围 | 用途 |
|----------|------|------|
| `Body_Height` | 0.0–1.0 | 身高调节 |
| `Body_Weight` | 0.0–1.0 | 体型胖瘦 |
| `Body_Muscle` | 0.0–1.0 | 肌肉/纤细程度 |
| `Body_Shoulders` | 0.0–1.0 | 肩宽 |
| `Face_Width` | 0.0–1.0 | 脸型宽窄 |
| `Face_Jaw` | 0.0–1.0 | 下颌形态 |

### 4.3 Morph Target 不在模型中时的行为

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

### 5.2 纹理（SHOULD）

AI 纹理生成（base_texture provider 的异步后台路径）生成全身 PBR albedo 纹理，作为 `Skin` 材质的 `map` 热替：

| 贴图类型 | 通道 | 分辨率 | 格式 |
|----------|------|--------|------|
| Albedo（反照率） | `map` | 1024×1024 | PNG sRGB |
| Normal（法线） | `normalMap` | 1024×1024 | PNG Linear |
| Roughness（粗糙度） | `roughnessMap` | 1024×1024 | PNG Linear |

纹理热替是非破坏性的——`CharacterController.setOutfit()` 先 dispose 旧纹理再加载新纹理，不修改骨骼动画或 morph targets。

---

## 6. 物种变体

5 个物种基底 + 1 个通用兜底。所有物种共享同一骨骼结构与动画命名——区别在外观 mesh、纹理与物种特有动画：

| 物种 | 文件名 | 特有动画 | 视觉特征 |
|------|--------|----------|----------|
| 人类 | `human.glb` | — | 写实人类比例、写实肤色 |
| 精灵 | `elf.glb` | `fly` SHOULD | 尖耳、修长体态、可选择翼/光效 |
| 灵兽 | `spirit_beast.glb` | `fly` SHOULD、`walk` 略呈四足感 | 兽形/半兽形、毛绒/鳞片纹理 |
| 机甲 | `mecha.glb` | — | 金属材质（`metalness: 0.8+`）、发光 `emissiveMap`、关节外露 |
| 幻形 | `shapeshifter.glb` | `fly` SHOULD | 半透明/非人形体、`transmission` 材质、流动光效 |
| 通用兜底 | `character.glb` | — | 中性人形，任何物种找不到时的 fallback |

引擎回退链：persona `biological_type` → 物种 GLB → 找不到时 → `character.glb` → 再找不到 → 程序化兜底角色。

---

## 7. 验证清单

模型提交前的检查项：

- GLB 2.0 单文件，< 5 MB
- 25 关节 Mixamo Humanoid 骨骼，关节名严格匹配 §2
- §3.1 的 9 个核心状态动画全部存在，命名正确
- §3.4 的 `walk` 动画存在
- 所有循环动画首尾帧无缝
- §4.1 的 ARKit morph targets 全部存在于 Body mesh 的 `extras.targetNames`
- `jawOpen` morph 存在（TTS 口型同步前置条件）
- `eyeBlinkLeft` / `eyeBlinkRight` morph 存在（自动眨眼前置条件）
- 两个材质槽 `Skin` + `Eyes`，命名正确
- 模型面朝 -Z，头朝 +Y
