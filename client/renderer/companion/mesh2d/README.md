# Mesh2D 渲染模块

桌面伙伴的 2D SkinnedMesh 渲染路径：服务端 `mesh2d_pipeline` 把立绘切成 6 个核心物理层（back_hair / body_main / front_hair / arm_L / arm_R / 可选 clothing），本模块加载 manifest + 部件 PNG，构造带骨骼的 SkinnedMesh，跑呼吸 / 眨眼 / 嘴型 / 头部跟随 / jiggle 弹簧动画。

## 模块结构

| 文件                | 职责                                                                                                           |
| ------------------- | -------------------------------------------------------------------------------------------------------------- |
| `Mesh2DCanvas.tsx`  | React 包装，挂载到 SpriteStage，构造 WebGLRenderer + OrthographicCamera + tick 循环                            |
| `mesh2d-runtime.ts` | 加载 manifest → 构建 Skeleton + SkinnedMesh；Z-sort 三层防御（depthWrite=false + renderOrder + 骨骼 Z 微偏移） |
| `mesh2d-bones.ts`   | 自研弹簧+阻尼 jiggle 物理（无 Box2D / cannon.js）                                                              |
| `mesh2d-loader.ts`  | manifest 缓存与 `$mesh2dReady` 状态门控                                                                        |
| `mesh2d-store.ts`   | `$mesh2dInfo`、`$renderMode`、`$mesh2dReady` atoms                                                             |

## 关键约束

- **零新运行时依赖**：复用现有 Three.js（与 `client/renderer/companion/3d/Engine.ts` 共享），不引入 Spine / PixiJS / 任何非 MIT 协议运行时。
- **严守红线**：head 旋转 ±15°、呼吸 scale ∈ [1.0, 1.015]、jiggle offset ±5px；超出此范围 8×8 段 PlaneGeometry 会出现网格剪切。
- **完全骨骼变形驱动五官**：眨眼 = eye_bone.scale.y 1→0.05→1；嘴型 = mouth_bone.scale.{x,y}。零贴图切换，100% 保留原画画风。
- **降级链路**：avatar 未确认 → 程序化蛋（`3d/CharacterController.createProcedural`）。avatar 已确认但 2D 不可用 → 程序化蛋。

## 与 SpriteStage 集成

`client/renderer/companion/root.tsx` 根据 `$renderMode` 挂 `<Mesh2DCanvas>` 或 `<Companion3D>`，二者互斥不共存。SpriteStage 的拖拽 / 戳 / 双击 / 鼠标穿透逻辑同时生效。

## WS 事件

- `companion.mesh2d.ready` → `$mesh2dInfo` 更新，canvas 自动重建场景
- `companion.mesh2d.failed` → 回退到程序化蛋（avatar 已确认但 2D 失败时由 SpriteStage 显示蛋）
- `companion.render_mode.changed` → `$renderMode` 切换，重建 canvas

## 已知限制

- 单 manifest 不支持多套表情/服装替换（后续若需要可扩展 `swap_sets` 字段）。
- 骨骼拓扑固定，pivot 由姿态估计动态计算；姿态误差大时五官位置会偏（极端姿态可能掉到 bbox 外）。
