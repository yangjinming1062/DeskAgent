# 2D 行水合 Store

`/api/companion/2d` 行的客户端镜像。渲染链为 puppet（PSD）单链（[puppet/](../puppet/)）；多手势识别在 [sprite/gesture-tracker.ts](../sprite/gesture-tracker.ts)，粒子特效在 [vfx.tsx](../vfx.tsx)。

## 文件

| 文件 | 职责 |
|------|------|
| `mesh2d-store.ts` | `$mesh2dInfo` / `$renderMode` / `$mesh2dHitmap`；`hydrateMesh2D`（GET /api/companion/2d + persona render_mode）、`requestMesh2DGeneration`（POST设置页重试入口）、`switchRenderMode`（幂等守卫防广播回环） |
| `psd-opfs-cache.ts` | `fetchPsdWithCache`（OPFS 本地缓存 + 远端拉取 + '8BPS' 魔术字节校验）；throwOnError=true，abort / 错误原样抛给 PuppetStage 的 try/catch |

- `$mesh2dHitmap` 命中总线：PuppetStage 写入（当前帧部件网格精确命中），SpriteStage / root 消费；`hit(nx, ny)` 只吃归一化坐标。
- 水合顺序恒为 `hydrateMesh2D()` → `hydratePuppet()` 串接（puppet 分流依赖行里的 manifest_url；manifest 恒为 `kind=psd` 描述符）。
- `companion.2d.failed` 经 `setMesh2DStatus('failed', reason)` 落状态；设置页按状态显示「重新切分」重试（DESIGN §5.5）。
