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

## 4. 管线清洗与规范化契约

Tripo3D 在 `spec=mixamo` 模式下生成的原始 GLB/FBX 通常具备以下特征：
- 骨骼命名自带 `mixamorig:` 前缀（例如 `mixamorig:Hips`、`mixamorig:Spine`）；
- 原始坐标系可能为侧向建模（双臂沿 Y 轴展开，正面朝向 -X）；
- 场景中可能附带未绑定的辅助几何体（如 `Icosphere`）。

**后端形变注入管线（`inject_morph_targets.py`）的规范化职责**：
1. **网格提取与清理**：精准筛选挂载在骨架上的蒙皮材质网格，剥离 `Icosphere` 等辅助体；
2. **坐标系前向对齐**：自动检测手臂展开方向，对侧向模型旋转对齐至前向标准坐标系（双臂沿 X 轴对称展开，正面朝向镜头）；
3. **命名清洗**：自动去除所有骨骼及对应蒙皮顶点组（Vertex Groups）的 `mixamorig:` 前缀，输出符合本规范的纯净 Mixamo 骨骼树；
4. **ARKit 形变注入**：基于 Head 骨骼局部坐标精确定位五官与面部区域，注入 44 组 ARKit Blendshapes。

---

## 5. API 调用参数

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

## 6. 参考实现

- 骨骼路由：`backend/services/companion/tripo_client.py`（`_RIG_SPECS`、
  `_RIG_MODEL_VERSIONS`、`rig_spec()`、`rig_model_version()`）
- 形变与规范化管线：`backend/assets/animations/inject_morph_targets.py`
- biped clip 库：`client/renderer/companion/3d/clips-biped.ts`（动画库中所有
  `bone` 常量）
