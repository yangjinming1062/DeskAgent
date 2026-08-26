# 2D 后端模块

2D 形象的拆分编排器：see-through 双 provider（HF 主用 / 魔搭备用）拆分为语义层 PSD（拆分实现见 [seethrough/](../seethrough/)），本模块负责行状态机、优先级队列、落库与 WS 事件；另持有 LLM 动作白名单。

## 模块结构

| 文件 | 职责 |
|------|------|
| `pipeline.py` | 拆分编排：see-through 调用 → `Companion2DModel` 落库（manifest / layers / content_hash）→ outfit 接缝翻转 → WS 事件 |
| `mesh2d_service.py` | 业务逻辑层：generate / query / set_render_mode / reset |
| `priority_queue.py` | LLM API 任务优先级调度（高 / 低） |
| `actions.py` | 2D 动作白名单（`DEFAULT_ACTIONS` / `NON_LLM_ACTIONS`，注入 LLM 动作词表；客户端 PuppetStage 动作包络按同名兑现） |

## 数据契约

- **`Companion2DModel`** 表（`companion_2d_models`）：`status`, `manifest_json`, `manifest_path`, `layers_json`, `content_hash`, `active`
- **manifest schema**：`spiritagent.2d.psd/1`（PSD 描述符，`kind=psd`）
- **WS 事件**：`companion.2d.ready` / `companion.2d.failed` / `companion.render_mode.changed`

## 关键约束

- **3D 用户后台排队的 2D 拆分**：render_mode='3d' 时 priority='low'；render_mode='2d' 时 priority='high'。同 key 旧任务会被取消接力。
- **失败兜底**：拆分失败时只置 row.status='failed'，前端 SpriteStage 显示程序化蛋；永不阻塞用户。
