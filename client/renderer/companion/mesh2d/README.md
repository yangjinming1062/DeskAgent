# 2D 行水合 Store

`/api/companion/2d` 行的客户端镜像。渲染链已收口为 puppet（PSD）单链——本目录曾承载的骨骼渲染链（Mesh2DCanvas 及 runtime / bones / drivers / hitmap / loader）已删除；多手势识别移至 [sprite/gesture-tracker.ts](../sprite/gesture-tracker.ts)，粒子特效移至 [vfx.tsx](../vfx.tsx)。

## 文件

| 文件 | 职责 |
|------|------|
| `mesh2d-store.ts` | `$mesh2dInfo` / `$renderMode` / `$mesh2dHitmap`；`hydrateMesh2D`（GET /api/companion/2d + persona render_mode）、`requestMesh2DGeneration`（POST，设置页重试入口）、`switchRenderMode`（幂等守卫防广播回环） |

- `$mesh2dHitmap` 命中总线：PuppetStage 写入（rig 六区），SpriteStage / root / 调试台消费；`hit(nx, ny)` 只吃归一化坐标。
- 水合顺序恒为 `hydrateMesh2D()` → `hydratePuppet()` 串接（puppet 分流依赖行里的 manifest_url；manifest 恒为 `kind=psd` 描述符）。
- `companion.2d.failed` 经 `setMesh2DStatus('failed', reason)` 落状态；设置页按状态显示「重新切分」重试（DESIGN §5.5）。
