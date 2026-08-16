# 伙伴 3D 模型与动画规格

> **谁需要读这份文档**：编写或审查 `client/renderer/companion/3d/clips-*.ts` 动画库、
> `client/renderer/companion/3d/MorphController.ts` morph 控制、以及任何新增 clip /
> morph / 材质通道的开发者。
>
> **怎么使用**：当作可执行的 checklist 而非教程——
>
> 1. 对照 §1 骨骼命名，确认 clip track 引用的 bone name 存在于对应 spec 的 GLB 节点里；
> 2. 对照 §2 动画 clip 目录，逐条核对命名、循环标志、时长；新加 clip 时也按 §2 的分类补；
> 3. 对照 §3 morph target 命名与分组；新加 morph 必须落到 ARKit 标准命名；
> 4. 对照 §4 材质通道清单做材质相关改动；
> 5. 改动完成后用 §5 checklist 自检一遍。
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
| `speaking` | 循环 | 3~4s | 嘴型由 `jawOpen` morph 实时驱动，clip 不含嘴部关键帧 |
| `working` | 循环 | 3~4s | 前倾、双手模拟打字 |
| `sleeping` | 循环 | 5~6s | 头倾、呼吸深缓；最慢循环 |
| `interacting` | 单次 | 1~1.5s | 被点击后的轻跳 + 回头 |
| `emotional_idle` | 循环 | 3~4s | 情绪保持期的身体循环；面部细节由 morph 叠加 |
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

### 2.4 移动（biped MUST walk + SHOULD = 4）

`walk`（循环 1~1.2s，步幅对应 60~100 px/s）· `idle_to_walk` · `walk_to_idle` · `fly`（循环 2~2.5s）· `drag`（单次 0.5s）

### 2.5 互动反应（biped SHOULD = 6）

被点击的反馈。缺失时退回 `interacting`。

`poke_light`（轻触怀疑）· `poke_heavy`（高强度惊跳）· `poke_happy`（贴人形反馈）· `poke_angry`（被骚扰）
· `poke_shy`（缩肩低头）· `drag_end`（拖拽结束松手回弹）

### 2.6 仪式动作（biped SHOULD = 3）

`greeting`（孵化 / 首次问候挥手）· `goodbye`（告别挥手）· `wake_up`（从 SLEEPING 唤醒揉眼）

### 2.7 正面情绪（biped SHOULD = 8）

`dance_happy` · `celebrate` · `giggle` · `cheer` · `clap` · `spin_happy` · `jump_joy` · `heart_pose`

### 2.8 负面情绪（biped SHOULD = 8）

`pout`（撅嘴撒娇）· `stomp_angry`（跺脚）· `sulk`（低头嘟嘴）· `cry`（抽泣）
· `tremble_fear`（发抖）· `collapse_sad`（低头沮丧）· `shake_frustration`（摇头甩手）· `withdrawal`（蜷缩后退）

### 2.9 社交（biped SHOULD = 6）

`wave_warm` · `bow` · `fold_arms`（循环）· `standing_relax`（循环）· `shrug` · `shake_head`

### 2.10 亲密互动（biped SHOULD = 10）

`hug_offer` · `hug_receive` · `kiss_lips` · `kiss_cheek` · `lap_pillow`（循环）
· `lean_on_shoulder`（循环）· `whisper` · `cuddle`（循环）· `hold_hand`（循环）· `pat_receive`

### 2.11 私密互动（biped SHOULD = 5）

`intimate_embrace`（循环）· `sleep_together`（循环）· `carry_princess`（循环）· `forehead_touch` · `nuzzle`

### 2.12 日常活动（biped SHOULD = 6）

`sit`（循环）· `eat`（循环）· `drink`（循环）· `read`（循环）· `pet_animal`（循环）· `exercise_stretch`（循环）

### 2.13 惊喜反应（biped SHOULD = 7）

`surprise_jump` · `shock_stepback` · `dizzy`（循环）· `embarrassed_cover` · `proud_pose` · `relieved_sigh` · `curious_lean`

### 2.14 安抚 / 疗愈（biped SHOULD = 6）

`comfort_pat` · `pat_head_give` · `wipe_tears` · `warm_smile` · `reassure_nod` · `hug_comfort`

### 2.15 天气 / 环境（biped SHOULD = 5）

`shiver_cold`（循环）· `fan_self` · `sneeze` · `rain_look` · `sunbathe`（循环）

### 2.16 负面情绪扩展（biped SHOULD = 5）

`glare` · `silent_treatment`（循环）· `disappointed_walk` · `jealous_pout` · `envy_sigh`

### 2.17 亲密扩展（biped SHOULD = 5）

`forehead_kiss` · `nose_boop` · `hand_kiss` · `spoon`（循环）· `piggyback`（循环）

### 2.18 音乐 / 舞蹈扩展（biped SHOULD = 3）

`dance_sway`（循环）· `dance_spin` · `conduct_music`（循环）

### 总览与性格标签驱动调度

- **骨骼静态库**：biped 库包含 **109 clip**（9 MUST + 100 SHOULD），其余 6 类 rig（quadruped / avian / serpentine / aquatic / hexapod / octopod）均含 **9 个规范状态 clip**（`idle` / `listening` / `thinking` / `speaking` / `working` / `sleeping` / `interacting` / `emotional_idle` / `disconnected`）及各自物种多骨骼关键帧动作，各 rig 库动作数均达 20~45+。
- **性格标签驱动调度**：每个 clip 可声明 `tags?: readonly string[]`（如 `["温柔", "活泼", "护主"]` 等），与伴侣当前性格标签求交集；在互动（poke / drag）、情绪状态切换与 idle 微动中优先命中重合度最高的动作，交集相同时随机挑选。
- **LLM 动画生成与动态注入**：后端基于 `POST /api/companion/animations/generate` 支持依据 rig_type、骨骼结构及性格标签动态生成专属关键帧并清洗校验；客户端通过 `$generatedClips` 及 `CharacterController.appendClipDefs()` 实现运行时动态加载。

---

## 3. Morph Target（44 个）

morph target 用 ARKit BlendShape 标准命名。所有 morph 由资源管线注入到 GLB，命名
必须落到下表分组。

### 3.1 基础表情（16）

| 部位 | 名称 |
|------|------|
| 眼 | `eyeBlinkLeft` `eyeBlinkRight` `eyeWideLeft` `eyeWideRight` `eyeSquintLeft` `eyeSquintRight` `eyesLookDown` |
| 眉 | `browInnerUp` `browInnerDown` |
| 嘴 | `jawOpen` `mouthSmile` `mouthSmileRight` `mouthFrown` |
| 其他 | `cheekSquintLeft` `noseSneerLeft` `tongueOut` |

### 3.2 负面 / 强烈情绪（14）

`eyeCloseTight` `eyeDroopLeft` `eyeDroopRight` `eyeWidenFear` `eyeNarrow`
`browFurrow` `browOuterUp` `nostrilFlare` `mouthTremble` `mouthCornerDown`
`jawClench` `lipPress` `faceWince` `cheekPuff`

### 3.3 亲密 / 俏皮（8）

`eyeCloseLeft` `eyeCloseRight` `browRaiseLeft` `browRaiseRight` `mouthPucker`
`lipBiteLower` `noseWrinkle` `cheekBlush`

### 3.4 体型调节（6）

`Body_Height` `Body_Weight` `Body_Muscle` `Body_Shoulders` `Face_Width` `Face_Jaw`
（均 0.0~1.0）

### 缺失时的退化行为

| 缺失 | 引擎行为 |
|------|----------|
| `eyeBlink*` | 不眨眼 |
| `jawOpen` | TTS 期间无嘴型同步 |
| 情绪 morph | 该情绪 fallback 为纯 idle 面部 |
| 体型 morph | 使用模型默认体型 |

---

## 4. 材质与装配

每个模型至少两个材质 slot：`Skin`（身体，roughness 0.55）· `Eyes`（眼珠，roughness 0.12）。
PBR 纹理由资源管线生成并嵌入 GLB，支持 5 通道（`albedo` / `normal` / `roughness` / `metalness` / `displacement`，客户端绑定到 `MeshStandardMaterial.displacementMap` 呈现微表面起伏与织纹/刺绣深度）。换装由后端单入口 `POST /api/companion/wardrobe/preview` 接收描述文本，由一次 LLM 路由调用决定走 `texture`（贴图热替换，仅改色/材质/图案）、`garment`（几何装配，加载独立服装 GLB 并 rebind 到身体骨骼）或 `accessory`（挂件挂到 socket 骨骼）流水线——客户端不暴露 `kind` 字段。三者均不改动身体骨骼或 morph。装配语义（kind / slot / layer / socket / physics / materials 映射）的协议契约见 [PROTOCOL.md §1.6](../PROTOCOL.md)。

### 4.1 换装单元 GLB 事实规范

- **garment GLB**：独立 skinned mesh + 身体 armature（导出时**复用身体 armature 对象本身**，不重建、不改名、不改顺序，保证 `skins[].joints` 骨骼名与顺序与身体 GLB 完全一致——客户端 rebind 零映射的前提）；**不含动画、不含 morph**。锚点顶点组 `VG_ANCHOR` 在导出前校验非空（生成管线内部契约，不出现在 GLB）。
- **accessory GLB**：纯静态 mesh（无 skins、无 armature），佩戴点在导出时已对准挂点骨骼的世界位置；无动画、无 morph。
- **extras**：装配元数据内嵌于 `scene.extras["dsh:assembly"]` 且同时落在各 mesh node 的 extras（兼容不导出 scene 自定义属性的导出器），与 DB `assembly_json` 一致（GLB 自描述，DB 为冲突仲裁）。

### 4.2 garment 生成管线确定性后处理参数

LLM 只产"毛坯几何 + `VG_ANCHOR` 锚点标注"，其后一切由确定性 bpy 代码接管（贴合/厚度/悬垂/蒙皮/防穿模是数值几何问题，必须可复现可校验）：

| 阶段 | 操作 | 参数 |
|------|------|------|
| fit | Shrinkwrap 仅作用于 `VG_ANCHOR` 顶点（保住裙摆/褶皱轮廓） | offset 1.5 mm |
| drape | Blender 布料重力悬垂仿真（`VG_ANCHOR` 钉住，身体碰撞，步进 20 帧将悬垂褶皱烘焙进单层静态几何） | frames 20，distance 3 mm |
| 厚度 | Solidify 向外 + 低密度补 Subsurf | 2 mm；顶点 < 4000 时 level=1 |
| 蒙皮 | Data Transfer 从身体 mesh 迁移顶点权重（最近面插值）+ ARMATURE 修改器——薄壳/悬空几何上自动权重必失败 | REPLACE ×1.0 |
| 碰撞 | 静止态顶点推出身体表面（BVH 最近点） | clearance 3 mm |

蒙皮用 Data Transfer 而非 `parent_set(ARMATURE_AUTO)`：自动权重基于骨骼热扩散，在薄壳（裙摆、褶皱）上必产生错误权重；Data Transfer 直接继承身体表面最近面的权重，对任意形态、任意物种成立。

### 4.3 拟真度上限与诚实边界

- **零边际成本的拟真上限**：多模态 LLM 程序化毛坯 + 确定性后处理（厚度/悬垂烘焙/权重传递）+ 5 通道 PBR（含 displacement）+ 运行时 CPU 表面碰撞（`BodyCollider`），可达成"可信、自然、有悬垂与立体微细节"的次世代卡通/二次元桌面伴侣效果。
- **与商业扫描级的边界**：几何精度无法达到商业 image-to-3D（Tripo/Rodin）按件计费或真实扫描/资产库的照片级精度。此为成本策略决定的保真度边界，而非工程缺陷；桌面精灵场景在此上限内即可达成设计目标。

---

## 5. 模型质量与压缩传输原则

### 5.1 模型质量第一原则（Quality Priority）

- **保留最高面数上限**：Tripo3D 等生成阶段启用最高面数上限（`tripo_face_limit` 最大化），以呈现高精度的发丝轮廓、服饰褶皱及细腻五官微结构；
- **禁止破坏性有损后处理**：形变注入与资产处理管线严禁对模型执行有损减面（Decimation）或有损量化（如 Draco Quantization），确保 3D 资产的几何曲面与贴图法线 100% 保真。

### 5.2 整文件无损压缩与客户端透明解压

- **无损整包压缩**：由于 44 组 ARKit 面部/体态 Blendshape 在非位移区域包含大量稀疏浮点数据，采用 Gzip / Deflate 等通用无损压缩方案可实现 90% 以上的体积缩减（原始 300MB~600MB 模型无损压缩后仅需 ~30MB~50MB 传输带宽）；
- **客户端透明解压**：客户端（[`CharacterController.ts`](../client/renderer/companion/3d/CharacterController.ts)）集成原生 Web 流式解压（`DecompressionStream`），自动识别压缩魔数并透明还原为原始二进制 GLB 缓冲区，零感知交付给 Three.js `GLTFLoader` 渲染。

---

## 6. 验证 checklist

审查 `clips-biped.ts`（或对应 rig_type 库）时：

- [ ] §2.1 的 9 个 MUST clip 全部存在且命名正确
- [ ] 每个 clip 引用的 bone name 在 §1 骨骼层级中存在
- [ ] 循环 clip 首尾帧值一致（无视觉跳变）
- [ ] 旋转值在合理范围（手臂 Z ±1.5、肩部 ±0.5、头部 ±0.3）
- [ ] morph target 命名覆盖 §3 全部名称
- [ ] `clips-registry.ts` 的 `getClipDefs(rigType)` 能正确路由到对应库
- [ ] 至少一个 walk / fly / sleep 状态的真实关键帧（非 placeholder 兜底）
