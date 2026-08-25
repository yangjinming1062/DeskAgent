# 2D 后端模块

AI 自动切分流水线把立绘切成 6 个核心物理层（back_hair / body_main / clothing / arm_L / arm_R / front_hair），输出 manifest + 部件 PNG，给客户端的 Three.js SkinnedMesh 用。

## 模块结构

| 文件 | 职责 |
|------|------|
| `pipeline.py` | 主流水线：see-through 优先拆分（配置门控，失败降级本链）→ 视觉 LLM 区域识别 → CPU 抠图 → 关键点 → 骨骼装配 → manifest 落盘 + WS 事件 |
| `region_detector.py` | 视觉 LLM 输出 6 部件 bbox；解析 + 校验（最少 5 层）|
| `pose_estimator.py` | 视觉 LLM 输出 20 关键点；含 `sanitize_keypoints()` 解剖学约束（对称性 + 中线 + 比例 + 缺关键点回退）|
| `layer_extractor.py` | 全图 rembg ONNX 抠图（异常降级白底形态学）→ bbox 裁切 → alpha 碎块过滤 / 孔洞填充 / 边缘羽化 → 收紧到内容框 → 按 z 序做像素归属（重叠区归最高 z 层，下层留 2px underlap） |
| `occlusion_resolver.py` | CPU 像素操作补全被遮挡区域（无 GPU Inpainting）|
| `skeleton_builder.py` | 关键点 + 部件 → 骨骼拓扑（pivot + skin weights）|
| `manifest_exporter.py` | manifest.json 序列化（schema `spiritagent.2d/1`）|
| `llm_validator.py` | 部件合理性校验（数量、面积）|
| `priority_queue.py` | LLM API 任务优先级调度（高 / 低）|
| `prompts.py` | 视觉 LLM prompt 模板（区域识别 + 关键点估计）|
| `mesh2d_service.py` | 业务逻辑层：generate / query / set_render_mode / reset |

## 数据契约

- **`Companion2DModel`** 表（`companion_2d_models`）：`status`, `manifest_json`, `manifest_path`, `layers_json`, `content_hash`, `active`
- **manifest schema**：`spiritagent.2d/1` — `canvas` / `camera` / `skeleton.bones[]` / `meshes[]` / `animations.{breath,blink,idle_sway,jiggle,red_lines,actions,idle_variants,locomotion}`
- **WS 事件**：`companion.2d.ready` / `companion.2d.failed` / `companion.render_mode.changed`

## 关键约束

- **零本地 GPU 依赖**：CPU 抠图走 rembg ONNX（`isnet-general-use`），权重缺失或推理异常时降级 `vectorized_matting` 白底形态学；抠图与后处理经线程池执行（秒级 CPU 推理，不占事件循环）。视觉 LLM 走现有 LLM 网关；图生图 API 不调用（眨眼 / 嘴型走骨骼 scale 变形）。
- **3D 用户后台排队的 2D 切分**：render_mode='3d' 时 priority='low'；render_mode='2d' 时 priority='high'。同 key 旧任务会被取消接力。
- **canvas 恒等于源立绘尺寸**：部件 origin 是归一化 bbox × canvas 而 plane 尺寸是源图像素，canvas 偏离源图会把部件中心拉开、拼装出缝隙（客户端按 canvas 布局，不感知源图）。
- **5 项工程优化落地**：6 层物理（不抠 eye/mouth）、`material.depthWrite=false` + `renderOrder` + 骨骼 Z 微偏移、眨眼 / 嘴型走骨骼 scale、`sanitize_keypoints()` 解剖学约束、严守旋转幅度红线（head ±15°、breath 1.0~1.015、jiggle ±5px）。
- **失败兜底**：流水线失败时只置 row.status='failed'，前端 SpriteStage 显示程序化蛋；永不阻塞用户。

## 依赖复用

- `vectorized_matting` (`backend/components/matting.py:131`)
- `save_companion_asset` / `signed_companion_asset_url` (`backend/services/companion/asset_store.py:80,119`)
- `load_avatar_bytes_as_data_uri` (`backend/services/companion/avatar_service.py`)
- `execute_with_fallback` + `resolve_vision_chain` (`backend/services/llm/`)
