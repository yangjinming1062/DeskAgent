# Tripo3D `spec=tripo` 骨骼命名

> 适用范围：`spec=tripo` 用于 biped + 其余 6 类 rig（quadruped / avian / serpentine / aquatic / hexapod / octopod）。

---

## 1. 命名约定

- **零关节前缀**：根骨骼叫 `Hips`（不是 `Root_Hips`）；
- **左右前缀**：`L_` / `R_`；
- **Twist 辅助骨**：四肢大段各多 1~2 个 `*Twist01` / `*Twist02`，用于平滑蒙皮变形，
  **不要**在动画 track 里直接引用，让蒙皮自动接管；
- **缺少 Eye / Jaw**：面部动画 100% 走 morph target，没有 Eye/Jaw 骨骼；
- **morph / blendshape**：Tripo 默认不生成；下游 morph 由资源管线注入。

---

## 2. 完整骨骼层级

### 2.1 Biped（41 关节）

```
Root
└──Hip
   ├──Pelvis
   └──Waist
      └──Spine01
         └──Spine02
            ├──NeckTwist01 → NeckTwist02 → Head
            ├──L_Clavicle → L_Upperarm → L_Forearm → L_Hand
            │   ├──L_ForearmTwist01 → L_ForearmTwist02
            │   └──L_UpperarmTwist01 → L_UpperarmTwist02
            ├──R_Clavicle → R_Upperarm → R_Forearm → R_Hand  (mirrored)
            │   ├──R_ForearmTwist01 → R_ForearmTwist02
            │   └──R_UpperarmTwist01 → R_UpperarmTwist02
            ├──L_Thigh → L_Calf → L_Foot → L_ToeBase
            │   ├──L_CalfTwist01 → L_CalfTwist02
            │   └──L_ThighTwist01 → L_ThighTwist02
            └──R_Thigh → R_Calf → R_Foot → R_ToeBase  (mirrored)
               ├──R_CalfTwist01 → R_CalfTwist02
               └──R_ThighTwist01 → R_ThighTwist02```

### 2.2 Quadruped

| 槽位 | 骨骼名 |
|------|--------|
| root | `Hips` |
| spine | `Spine`, `Spine1`, `Spine2`, `Neck` |
| head | `Head` |
| jaw | `Jaw` |
| leftFrontLeg | `LeftFrontLeg`, `LeftFrontKnee`, `LeftFrontFoot` |
| rightFrontLeg | `RightFrontLeg`, `RightFrontKnee`, `RightFrontFoot` |
| leftHindLeg | `LeftHindLeg`, `LeftHindKnee`, `LeftHindFoot` |
| rightHindLeg | `RightHindLeg`, `RightHindKnee`, `RightHindFoot` |
| tail | `Tail`, `Tail1`, `Tail2` |

### 2.3 Avian

预期槽位：`Hips`, `Spine`, `Spine1`, `Neck`, `Head`, `LeftWingShoulder`,
`LeftWing`, `LeftWingEnd`, `RightWing*`（镜像）, `LeftLeg` / `RightLeg`,
`LeftFoot` / `RightFoot`, `Tail` / `Tail1`。具体名称以实际 GLB 为准——首次接入时
用 `services/companion/rig_exploration.py` 解析并回填到本节。

### 2.4 Serpentine

预期槽位：`Hips`, 一连串 `Spine` / `Spine1` ... `SpineN`（躯干主要靠躯干骨驱动），
`Neck`, `Head`, `Jaw`；无四肢。具体名称以实际 GLB 为准。

### 2.5 Aquatic

预期槽位：`Hips`（或 `Body`）, `Spine`, `Spine1`, ..., `Head`, `Jaw`（嘴）,
背鳍 `TopFin` / 胸鳍 `LeftFin` / `RightFin` / 尾鳍 `Tail` / `Tail1` / `Tail2`。
具体名称以实际 GLB 为准。

### 2.6 Hexapod

预期槽位：`Hips`, `Spine`, `Neck`, `Head`, 三对腿（每条 3 节）：
`LeftFrontLeg` / `LeftMidLeg` / `LeftBackLeg`，每条分 `Upper` / `Lower` /
`Foot`。具体名称以实际 GLB 为准。

### 2.7 Octopod

预期槽位：`Hips`（或 `Body`），八条触手 `Tentacle1` ... `Tentacle8`，每条
分 `Base` / `Mid` / `Tip`，头 `Head`。具体名称以实际 GLB 为准。

> 缺骨骼时的兜底：如果模型实际只有一部分骨骼（如 Tail 缺失），动画 track 跳过缺失
> 的骨骼即可，mixer 不会报错。

---

## 3. 动画库引用规则

| rig_type | 必须存在的骨骼（用于 clip track） |
|----------|--------------------------------|
| biped | `Hips` / `Spine01` / `Spine02` / `NeckTwist01` / `NeckTwist02` / `Head` / 四肢链 / `L_ToeBase` / `R_ToeBase` |
| quadruped | `Hips` / 至少一节 `Spine*` / `Head` / 四腿链 / 可选 `Tail*` |
| avian | `Hips` / `Spine*` / `Neck` / `Head` / 至少一侧翅膀 / `Tail*`（首次接入后回填） |
| serpentine | `Hips` / `Spine*` / `Head` / 可选 `Jaw` |
| aquatic | `Hips`（或 `Body`）/ `Spine*` / `Head` / `Tail*` / 可选 `*Fin` |
| hexapod | `Hips` / `Spine` / `Neck` / `Head` / 三对腿 |
| octopod | `Hips` / `Tentacle*` / `Head` |

**禁用模式**：不要引用 `*Twist01` / `*Twist02`——它们是蒙皮辅助骨，clip 设计不应
绕过蒙皮自动接管。

---

## 4. API 调用参数

Tripo3D `POST /v3/animations/rig`：

```json
{
  "input": "<上一阶段 image-to-model 的 task_id>",
  "rig_type": "<biped | quadruped | avian | serpentine | aquatic | hexapod | octopod>",
  "spec": "tripo",
  "model": "v2.5-20260210"
}
```

参数路由实现在 `backend/services/llm/providers/tripo/client.py::rig_spec` / `rig_model_version`（`_RIG_SPECS` / `_RIG_MODEL_VERSIONS` 常量是唯一权威）。

---

## 5. 参考实现

- 骨骼路由：`backend/services/llm/providers/tripo/client.py`（`_RIG_SPECS`、
  `_RIG_MODEL_VERSIONS`、`rig_spec()`、`rig_model_version()`）
- 各 rig_type 的 clip 库：`client/renderer/companion/3d/clips-<rig_type>.ts`，
  顶部 `*_BONES` 常量定义实际槽位映射
- 缺失骨骼的兜底：`_placeholder` / `*Manifest` helper 生成 idle 微动，确保
  任何 GLB 都能播放
