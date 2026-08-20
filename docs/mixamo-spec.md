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

**禁用模式**：不要在动画 track 里引用 `mixamorig_*` 前缀;Tripo 在云端 rig 链路上可能仍以该前缀生产 GLB,供应商需直出零前缀或由 Tripo SDK 自带清洗钩子。

---

## 4. 供应商责任

Tripo3D 在 `spec=mixamo` 模式下输出的 GLB 在云端 rig 链路上由 Tripo(`POST /v3/animations/rig`)保证直出合规:命名零前缀、几何仅含挂载骨架的蒙皮网格(无 `Icosphere` 等未绑定辅助体)、坐标系按本规范前向对齐。客户端 GLB 加载即信任 SPEC,后端不做任何内省或校验;3D 模型无表情 morph,面部动画由聊天窗表情头像承载。

---

## 5. API 调用参数

Tripo3D `POST /v3/animations/rig`:

```json
{
  "input": "<上一阶段 image-to-model 的 task_id>",
  "rig_type": "biped",
  "spec": "mixamo",
  "model": "v1.0-20240301"
}
```

参数路由在 `backend/services/image_to_3d/providers/tripo/client.py` 的 `rig_spec` / `_RIG_SPECS` 常量里(`SUPPORTS_RIGGING=True` 决定该跳是否进入 chain)。

---

## 6. 参考实现

- 骨骼路由:`backend/services/image_to_3d/providers/tripo/client.py`(`_RIG_SPECS` / `rig_spec()`)
- 能力链编排:`backend/services/companion/pipeline.py::run_capability_chain`
- biped clip 库:`client/renderer/companion/3d/clips-biped.ts`(动画库中所有 `bone` 常量)
