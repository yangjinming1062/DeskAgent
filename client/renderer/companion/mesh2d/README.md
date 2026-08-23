# Mesh2D 渲染模块

桌面伙伴的 2D SkinnedMesh 渲染路径：服务端 `mesh2d_pipeline` 把立绘切成 6 个核心物理层（back_hair / body_main / front_hair / arm_L / arm_R / 可选 clothing），本模块加载 manifest + 部件 PNG，构造带骨骼的 SkinnedMesh，跑呼吸 / 眨眼 / 嘴型 / 头部跟随 / jiggle 弹簧 / LLM 驱动 action / locomotion 复合躯干 / 子区域命中 impulse 等动画。

## 模块结构

| 文件 | 职责 |
|---|---|
| `Mesh2DCanvas.tsx` | React 包装，挂载到 SpriteStage，构造 WebGLRenderer + OrthographicCamera + tick 循环 |
| `mesh2d-runtime.ts` | 加载 manifest → 构建 Skeleton + SkinnedMesh；Z-sort 三层防御（depthWrite=false + renderOrder + 骨骼 Z 微偏移）；支持 eyeSquint 闭眼享受 |
| `mesh2d-bones.ts` | 自研弹簧+阻尼 jiggle 物理（无 Box2D / cannon.js） |
| `mesh2d-drivers.ts` | **动作 / locomotion / idle pose 调度器**：订阅 `$spriteState/$spriteEmotion/$spriteAction`，按 DESIGN §2.3 四层优先级（Active Action > Locomotion > Idle Variant > Base Micro-motion）合成骨骼 transform；提供 `triggerImpulse()` 给 hitmap 触发 jiggle |
| `mesh2d-hitmap.ts` | **子区域命中检测**：从 manifest.bones + meshes 缓存归一化 [0,1] bbox；`hitRegion(nx, ny)` 返回 head / face / arm_L/R / body / back_hair / front_hair / skirt；命中 hair 类区域时自动 `triggerImpulse` |
| `mesh2d-gestures.ts` | **多手势识别器**：光标在 head/face 横向往复检测触发摸头（head patting）；狂甩检测触发眩晕（dizzy） |
| `mesh2d-vfx.tsx` | **视觉特效与情绪粒子系统**：挂载在 SpriteStage 上层，提供爱心、怒气、冷汗、眩晕星环、音符与睡眠气泡粒子反馈 |
| `mesh2d-loader.ts` | manifest 缓存与 `$mesh2dReady` 状态门控 |
| `mesh2d-store.ts` | `$mesh2dInfo`、`$renderMode`、`$mesh2dReady` atoms |

## 关键约束

- **零新运行时依赖**：复用现有 Three.js（与 `client/renderer/companion/3d/Engine.ts` 共享），不引入 Spine / PixiJS / 任何非 MIT 协议运行时。
- **严守红线**：head 旋转 ±15°（≈0.26 rad）、呼吸 scale ∈ [1.0, 1.015]、jiggle offset ±5px、shoulder/elbow/wrist rotation ∈ ±π/2（≈90°）。driver 在写入每个 bone.rotation 后立即 `clampBoneTransform()` 兜底。
- **单位硬约束**：manifest 的 `rotation_rad` 字段命名强约束"弧度"，消费端 Three.js 用 `.rotation` 直接赋值，**不**调用 `degToRad`。pose 表里的所有数值已校验为弧度。
- **完全骨骼变形驱动五官**：眨眼 = eye_bone.scale.y 1→0.05→1；嘴型 = mouth_bone.scale.{x,y}；摸头 = eyeSquint (scale.y = 0.15)。零贴图切换，100% 保留原画画风。
- **走路 / 跳跃 / 物理落体**：下半身在 `body_main.png` 内无法独立旋转，walk/walk_fast 用 `body_main` 左右倾斜 + 上下 bob + `shoulder_L/R` 反向摆动 + `skirt/back_hair` 持续 impulse；jump 用 body_main 短暂 squash + shoulder 上扬脉冲；空中释放走自由落体加速度并在触地时触发 `land_squash` 弹性反弹。
- **降级链路**：avatar 未确认 → 程序化蛋（`3d/CharacterController.createProcedural`）。avatar 已确认但 2D 不可用 → 程序化蛋。

## 与 SpriteStage 集成

`client/renderer/companion/root.tsx` 根据 `$renderMode` 挂 `<Mesh2DCanvas>` 或 `<Companion3D>`，二者互斥不共存。SpriteStage 的拖拽 / 物理抛掷 / 贴边吸附 / 摸头手势 / 戳 / 双击 / 鼠标穿透逻辑同时生效。子区域命中由 `mesh2d-hitmap.ts` 提供，手势由 `mesh2d-gestures.ts` 识别，粒子层由 `mesh2d-vfx.tsx` 挂载展示。

## WS 事件

- `companion.mesh2d.ready` → `$mesh2dInfo` 更新，canvas 自动重建场景
- `companion.mesh2d.failed` → 回退到程序化蛋（avatar 已确认但 2D 失败时由 SpriteStage 显示蛋）
- `companion.render_mode.changed` → `$renderMode` 切换，重建 canvas
- `companion.affect` / `message.complete` 的 `affect.action` → `mesh2d-drivers` 解析为骨骼 pose 切换

## Manifest schema（v2）

`backend/services/companion/mesh2d/manifest_exporter.py` 输出的 `animations` 字段除原有 breath / blink / idle_sway / jiggle 外，包含：

- `actions`: 程序化骨骼 pose 表（含 `wave_right/left`、`present_right/left`、`petting`、`dizzy`、`fall`、`land_squash`、`peeking` 等）。白名单见 [backend/services/companion/mesh2d/prompts.py:action_prompt_section()](backend/services/companion/mesh2d/prompts.py)。
- `idle_variants`: idle 时按权重随机切换的 pose key 列表。
- `locomotion`: `still / walk / walk_fast / fly / drag / jump / fall` 的骨骼相位公式与 jump/fall 参数。

详见 [DESIGN.md §2.3](DESIGN.md) 的"四层叠加"。

## 已知限制

- 骨骼拓扑固定，pivot 由姿态估计动态计算；姿态误差大时五官位置会偏（极端姿态可能掉到 bbox 外）。
- 单 manifest 不支持多套服装 / 表情 swap（后续若需要可扩展 `swap_sets` 字段）。
