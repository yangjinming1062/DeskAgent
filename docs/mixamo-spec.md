# Tripo3D `spec=mixamo` 骨骼命名

> 适用范围：`spec=mixamo` 仅用于 biped。

---

## 1. 命名约定

- **根骨骼叫 `Hips`**（不是 `Root` / `Root_Hips`）；
- **左右用全拼**：`Left` / `Right`；
- **没有 Twist 骨**：骨骼层级扁平，蒙皮靠三角面权重处理；
- **有 Eye / Jaw**：`Head` 下挂 `LeftEye` / `RightEye` / `Jaw`，面部动画可控；
- **三节脊柱**：`Spine` -> `Spine1` -> `Spine2`，躯干扭转 / 呼吸主要靠
  Spine1 / Spine2。

---

## 2. 完整骨骼层级（Biped, 25 关节）

```
Hips
├──LeftUpLeg → LeftLeg → LeftFoot → LeftToeBase
├──RightUpLeg → RightLeg → RightFoot → RightToeBase
└──Spine
   ├──Spine1
      └──Spine2
         ├──LeftShoulder → LeftArm → LeftForeArm → LeftHand
         ├──RightShoulder → RightArm → RightForeArm → RightHand
         ├──Neck
            └──Head
               ├──LeftEye
               ├──RightEye
               └──Jaw
```

---

## 3. 动画库引用规则

| 用途 | 必引骨骼 |
|------|---------|
| 全身呼吸 / 重心 | `Hips`, `Spine`, `Spine1`, `Spine2` |
| 走路 / 跑步 | `LeftUpLeg`, `LeftLeg`, `LeftFoot`, `LeftToeBase`（右侧对称） |
| 手臂动作 | `LeftShoulder`, `LeftArm`, `LeftForeArm`, `LeftHand`（右侧对称） |
| 头部朝向 | `Neck`, `Head`, `LeftEye`, `RightEye` |
| 嘴型（fallback） | `Jaw` |
| 双手交叉动作 | `Spine2` 用来补偿躯干旋转 |

**禁用模式**：不要在动画 track 里引用 `mixamorig_*` 前缀（Blender FBX 导出残留），
必须用干净命名。

---

## 4. API 调用参数

Tripo3D `POST /v3/animations/rig`：

```json
{
  "input": "<上一阶段 image-to-model 的 task_id>",
  "rig_type": "biped",
  "spec": "mixamo",
  "model": "v1.0-20240301"
}
```

参数路由实现在 `backend/services/companion/tripo_client.py::rig_spec` / `rig_model_version`（`_RIG_SPECS` / `_RIG_MODEL_VERSIONS` 常量是唯一权威）。

---

## 5. 参考实现

- 骨骼路由：`backend/services/companion/tripo_client.py`（`_RIG_SPECS`、
  `_RIG_MODEL_VERSIONS`、`rig_spec()`、`rig_model_version()`）
- biped clip 库：`client/renderer/companion/3d/clips-biped.ts`（动画库中所有
  `bone` 常量）
