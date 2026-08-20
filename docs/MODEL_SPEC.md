# 伙伴 3D 模型与动画规格

> **谁需要读这份文档**：编写或审查 `client/renderer/companion/3d/clips-*.ts` 动画库、
> 以及任何新增 clip / 材质通道的开发者。
>
> **怎么使用**：当作可执行的 checklist 而非教程——
>
> 1. 对照 §1 骨骼命名，确认 clip track 引用的 bone name 存在于对应 spec 的 GLB 节点里；
> 2. 对照 §2 动画 clip 目录，逐条核对命名、循环标志、时长；新加 clip 时也按 §2 的分类补；
> 3. 对照 §3 材质通道清单做材质相关改动；
> 4. 改动完成后用 §4 checklist 自检一遍。
>
> **范围声明**：本文档只描述动画与模型设计层面的事实约束（骨骼、clip、morph、材质），
> 不覆盖「这些资源从哪里来」——生成管线、API 调用、TLS、持久化等都在代码里。
> Tripo3D 原生骨骼命名（`spec=tripo`，非 biped）的完整对照见
> [tripo-spec.md](tripo-spec.md)；Mixamo 骨骼命名（`spec=mixamo`，biped）见
> [mixamo-spec.md](mixamo-spec.md)。

---

## 1. 骨骼

动画 clip 的 `tracks` 用骨骼名作 key（如 `Head.quaternion`）。骨骼名必须与最终 GLB
`nodes[].name` 一致，否则 Three.js mixer 静默跳过该 track。

### 1.1 Biped（`spec=mixamo`）

完整层级与各 spec 的差异详见 [mixamo-spec.md](mixamo-spec.md)。动画库必须遵守的
最小集合：

- `Hips` 是根骨骼，所有位移 / 重心动画的 base；
- `Spine1` / `Spine2` 必须存在——用于躯干扭转 / 呼吸；
- `LeftShoulder` / `LeftArm` / `LeftForeArm` / `LeftHand`（右侧对称）必须存在；
- `LeftUpLeg` / `LeftLeg` / `LeftFoot` / `LeftToeBase`（右侧对称）必须存在——走路、
  跑步、坐下等下肢 clip 的基础；
- `Head` / `LeftEye` / `RightEye` 必须存在——眼动 + look-at 依赖；
- `Jaw` 可选——mouth fallback，没有时降级为静态嘴型。

### 1.2 非 Biped（`spec=tripo`）

7 类 rig 中 biped 用 Mixamo，其余 6 类（quadruped / avian / serpentine / aquatic /
hexapod / octopod）用 Tripo 原生骨骼。各类的完整节点树与具体骨骼名以
[tripo-spec.md](tripo-spec.md) 为准，命名约定：

- **零关节前缀**：`Hips`（无 `Hips_` 这种重命名）；
- **左右前缀**：`L_` / `R_`（区别于 Mixamo 的 `Left` / `Right`）；
- **Twist 辅助骨**：Tripo 在大腿 / 小腿 / 大臂 / 小臂上各多 1~2 个 Twist01/02，
  用于平滑蒙皮变形——动画 track 不要单独引用 Twist 骨骼，让蒙皮自动接管；
- **缺少 Eye / Jaw**：Tripo 原生 biped 也没有 Eye/Jaw 骨骼，
  面部动画 100% 走 morph target。

### 1.3 坐标系与四元数约定

- Y 轴朝上，模型朝向 -Z（glTF 标准），T-pose 为初始姿态；
- 旋转一律用欧拉角 `[x, y, z]` 转出的四元数（XYZ 旋转顺序）；
- 参考范围：手臂绕 Z 轴 ±1.5、肩部 ±0.5、头部 ±0.3——超出此范围通常意味着 clip
  设计错误或骨骼名误用。

---

## 2. 动画 clip 目录

clip 按骨骼类型分库存放（`clips-biped.ts` / `clips-quadruped.ts` / ……），
`clips-registry.ts` 按 `rig_type` 路由。命名约定：snake_case 小写，循环 clip 首尾帧
值一致（无视觉跳变），单次 clip 播完自动 crossFade 回 `idle`（0.3~0.5s）。

缺失 clip 时的退化行为：

| 缺失类别 | 退化到 |
|------|----------|
| SHOULD 类别 clip | 静默降级到 `idle`，UI 不报错 |
| MUST 类别 clip（核心状态） | UI 报错 + 显示一个程序化占位角色 |

### 2.1 核心状态（biped MUST = 9）

| clip | 类型 | 时长 | 动作要点 |
|------|------|------|----------|
| `idle` | 循环 | 4~5s | 腹呼吸、重心微移；用户 80% 时间看到 |
| `listening` | 循环 | 3~4s | 头微偏向用户、身体前倾 |
| `thinking` | 循环 | 3~4s | 手托下巴、目光上移 |
| `speaking` | 循环 | 3~4s | 躯干 + 头部微动；clip 不含嘴部关键帧（无 morph 驱动口型） |
| `working` | 循环 | 3~4s | 前倾、双手模拟打字 |
| `sleeping` | 循环 | 5~6s | 头倾、呼吸深缓；最慢循环 |
| `interacting` | 单次 | 1~1.5s | 被点击后的轻跳 + 回头 |
| `emotional_idle` | 循环 | 3~4s | 情绪保持期的身体循环（情绪面部由表情头像承载） |
| `disconnected` | 循环 | 4~5s | 懒腰、慢头、走神 |

### 2.2 微弱动作（biped SHOULD = 6）

IDLE 中每 10~15s 随机插入；缺失时退回 `idle`。

`idle_look_around` · `idle_blink` · `idle_stretch` · `idle_shift_weight` · `idle_yawn` · `idle_fidget`

### 2.3 场景 idle（biped SHOULD = 6）

按场景应用切换；缺失时退回 `idle`。

| clip | 适用场景 |
|------|----------|
| `idle_humming` | 通用放松 |
| `idle_dreamy` | 通用放空 |
| `idle_typing` | IDE |
| `idle_bounce` | Music |
| `idle_calm` | Reader |
| `idle_engaged` | Gaming |

### 2.4 移动（biped = 4）

`walk`（循环 1~1.2s，步幅对应 60~100 px/s）· `jump`（单次 1s，蹲下→起跳→滞空→落地）· `fly`（循环 2~2.5s）· `drag`（循环 1.6s）

### 2.5 互动反应（biped = 4）

被点击的反馈。缺失时退回 `interacting`。

`poke_light`（轻触怀疑）· `poke_heavy`（高强度惊跳）· `poke_happy`（贴人形反馈）· `drag_end`（拖拽结束松手回弹）

### 2.6 仪式动作（biped = 2）

`greeting`（孵化 / 首次问候挥手）· `goodbye`（告别挥手）

### 2.7 正面情绪（biped = 1）

`clap`（双手前伸合击）。其他正面情绪 clip（`dance_happy` / `celebrate` / `giggle` / `cheer` / `spin_happy` / `jump_joy` / `heart_pose`）不在内置库内，触发时静默降级到 `emotional_idle`。

### 2.8–2.18 类别说明

负面情绪 / 亲密互动 / 私密互动 / 日常活动 / 惊喜反应 / 安抚 / 天气 / 负面扩展 / 亲昵扩展 / 音乐舞蹈扩展等 SHOULD 类别在 biped 库内未提供 clip；`clip-dispatch.ts` 命中这些类别时静默回退到 `emotional_idle`。如需为某个具体类别补手写关键帧，按 §6 流程新增即可。

### 总览与性格标签驱动调度

- **骨骼静态库**：biped 库共 **37 clip**（9 MUST + 12 idle micro/context + 4 移动 + 4 互动 + 2 仪式 + 1 正面 + 5 社交），其中大部分为手写关键帧；其余 6 类 rig（quadruped / avian / serpentine / aquatic / hexapod / octopod）均含 **9 个规范状态 clip**（`idle` / `listening` / `thinking` / `speaking` / `working` / `sleeping` / `interacting` / `emotional_idle` / `disconnected`）及各自物种多骨骼关键帧动作，各 rig 库动作数均达 20~45+。
- **性格标签驱动调度**：每个 clip 可声明 `tags?: readonly string[]`（如 `["温柔", "活泼", "护主"]` 等），与伴侣当前性格标签求交集；在互动（poke / drag）、情绪状态切换与 idle 微动中优先命中重合度最高的动作，交集相同时随机挑选。
- **LLM 动画生成与动态注入**：后端基于 `POST /api/companion/animations/generate` 支持依据 rig_type、骨骼结构及性格标签动态生成专属关键帧并清洗校验；客户端通过 `$generatedClips` 及 `CharacterController.appendClipDefs()` 实现运行时动态加载。

---

## 4. 材质与渲染

每个模型至少两个材质 slot：`Skin`（身体，roughness 0.55）· `Eyes`（眼珠，roughness 0.12）。
PBR 纹理由资源管线生成并嵌入 GLB，支持原生 PBR 材质渲染。

### 4.1 渲染风格按类人面孔分流

- **渲染风格按类人面孔分流**：类人面孔的模型以二次元画风作为原图种子（全身立绘生成与画风选择按同一分流措辞，默认「精美二次元」，目的：规避写实人像在 3D 重建后的恐怖谷）；非人生物保留写实路线（无恐怖谷问题）。生成的 3D GLB 在客户端统一以 GLB 原生 PBR 路径渲染；画风信息保留在后端 `fullbody_style` 字段供立绘生成使用。
- **拟真度上限与边界**：生成的 3D 模型由供应商图生 3D 管线产出，在客户端经 Three.js 实时渲染。几何精度与贴图质量受图生 3D 供应商能力支配，桌面精灵场景在此上限内即可达成设计目标。

---

## 5. 模型质量与压缩传输原则

### 5.1 模型质量第一原则（Quality Priority）

- **保留最高面数上限**：Tripo3D 等生成阶段启用最高面数上限（`tripo_face_limit` 最大化），以呈现高精度的发丝轮廓、服饰褶皱及细腻五官微结构；
- **GLB 形态由供应商直接产出**：供应商负责几何曲面与贴图法线 100% 保真；后端不做云端 rig 兜底、morph 注入、draco 量化、减面或 SPEC 校验——云端产物即最终产物,直接落盘交付。

### 5.2 整文件无损压缩与客户端透明解压

- **无损整包压缩**：GLB 二进制缓冲区（蒙皮权重、morph 顶点位移、对齐补零等稀疏 / 重复数据占大头）采用 Gzip / Deflate 等通用无损压缩方案可实现 90% 以上的体积缩减（原始 300MB~600MB 模型无损压缩后仅需 ~30MB~50MB 传输带宽）；
- **客户端透明解压**：客户端（[`CharacterController.ts`](../client/renderer/companion/3d/CharacterController.ts)）集成原生 Web 流式解压（`DecompressionStream`），自动识别压缩魔数并透明还原为原始二进制 GLB 缓冲区，零感知交付给 Three.js `GLTFLoader` 渲染。

---

## 6. 验证 checklist

审查 `clips-biped.ts`（或对应 rig_type 库）时：

- [ ] §2.1 的 9 个 MUST clip 全部存在且命名正确
- [ ] 每个 clip 引用的 bone name 在 §1 骨骼层级中存在
- [ ] 循环 clip 首尾帧值一致（无视觉跳变）
- [ ] 旋转值在合理范围（手臂 Z ±1.5、肩部 ±0.5、头部 ±0.3）
- [ ] `clips-registry.ts` 的 `getClipDefs(rigType)` 能正确路由到对应库
- [ ] 至少一个 walk / fly / sleep 状态的真实关键帧（非 placeholder 兜底）
