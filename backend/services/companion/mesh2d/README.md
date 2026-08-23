# Mesh2D 后端模块

AI 自动切分流水线把立绘切成 6 个核心物理层（back_hair / body_main / clothing / arm_L / arm_R / front_hair），输出 manifest + 部件 PNG，给客户端的 Three.js SkinnedMesh 用。

## 模块结构

| 文件 | 职责 |
|------|------|
| `pipeline.py` | 主流水线：调视觉 LLM 区域识别 → CPU 抠图 → 关键点 → 骨骼装配 → manifest 落盘 + WS 事件 |
| `region_detector.py` | 视觉 LLM 输出 6 部件 bbox；解析 + 校验（最少 5 层）|
| `pose_estimator.py` | 视觉 LLM 输出 20 关键点；含 `sanitize_keypoints()` 解剖学约束（对称性 + 中线 + 比例 + 缺关键点回退）|
| `layer_extractor.py` | 在 bbox 内做白底毫秒级形态学抠图 + 形态学闭运算去毛糙 |
| `occlusion_resolver.py` | CPU 像素操作补全被遮挡区域（无 GPU Inpainting）|
| `skeleton_builder.py` | 关键点 + 部件 → 骨骼拓扑（pivot + skin weights）|
| `manifest_exporter.py` | manifest.json 序列化（schema `spiritagent.mesh2d/1`）|
| `llm_validator.py` | 部件合理性校验（数量、面积）|
| `priority_queue.py` | LLM API 任务优先级调度（高 / 低）|
| `prompts.py` | 视觉 LLM prompt 模板（区域识别 + 关键点估计）|
| `mesh2d_service.py` | 业务逻辑层：generate / query / set_render_mode / reset |

## 数据契约

- **`Mesh2DModel`** 表：`status`, `manifest_json`, `manifest_path`, `layers_json`, `content_hash`, `active`
- **manifest schema**：`spiritagent.mesh2d/1` — `canvas` / `camera` / `skeleton.bones[]` / `meshes[]` / `animations.{breath,blink,idle_sway,jiggle}`
- **WS 事件**：`companion.mesh2d.ready` / `companion.mesh2d.failed` / `companion.render_mode.changed`

## 关键约束

- **零本地 GPU 依赖**：CPU 抠图走 `vectorized_matting`；视觉 LLM 走现有 LLM 网关；图生图 API 不调用（眨眼 / 嘴型走骨骼 scale 变形）。
- **3D 用户后台排队的 2D 切分**：render_mode='3d' 时 priority='low'；render_mode='2d' 时 priority='high'。同 key 旧任务会被取消接力。
- **5 项工程优化落地**：6 层物理（不抠 eye/mouth）、`material.depthWrite=false` + `renderOrder` + 骨骼 Z 微偏移、眨眼 / 嘴型走骨骼 scale、`sanitize_keypoints()` 解剖学约束、严守旋转幅度红线（head ±15°、breath 1.0~1.015、jiggle ±5px）。
- **失败兜底**：流水线失败时只置 row.status='failed'，前端 SpriteStage 显示程序化蛋；永不阻塞用户。

## 依赖复用

- `vectorized_matting` (`backend/components/matting.py:131`)
- `save_companion_asset` / `signed_companion_asset_url` (`backend/services/companion/asset_store.py:80,119`)
- `load_avatar_bytes_as_data_uri` (`backend/services/companion/avatar_service.py`)
- `STYLE_CATALOG` (`backend/services/companion/fullbody_style_catalog.py:14`)
- `execute_with_fallback` + `resolve_vision_chain` (`backend/services/llm/`)
