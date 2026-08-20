# 3D 模型生成能力链

> **谁需要读这份文档**:后端 `services/companion/pipeline.py` 的维护者、新供应商接入方、客户端进度事件消费者。

3D 模型流水线由 web 进程内的 `pipeline.run_capability_chain` 直接驱动,长任务以 `asyncio.create_task` 跑在同一事件循环里。所有"绑骨 / 动画绑定 / 命名规范化 / morph 注入"由图生 3D 供应商自身完成;后端只编排链、保存云端产物——**不解析 GLB、不校验骨架集、不校验 morph、不做任何几何变换**。

## 1. 链拓扑

```
submit(seed) → task_id_a
poll(task_id_a) → ready
download(task_id_a) → glb_bytes
[provider.SUPPORTS_RIGGING?]      → provider.start_rig(task_id_a, rig_type)        → task_id_b
                                poll(task_id_b) → ready
                                download(task_id_b) → glb_bytes
[provider.SUPPORTS_ANIMATE_BIND?] → provider.start_animate_bind(task_id_b)         → task_id_c
                                  poll(task_id_c) → ready
                                  download(task_id_c) → glb_bytes
落盘 → companion-models/<uid>/<sha>.glb
```

- **`task_id` 串联,bytes 不跨 hop 传递**:GLB bytes 只在"下载到本进程内存"时短暂存在,终产物才落盘(`asset_store.save_companion_model`)。
- **`start_animate_bind` 的 `task_id` 是"已绑骨模型"的 `task_id`**:Tripo 的 `POST /v3/animations/retarget` 入参是前一步绑骨产物的 `task_id`(在 Tripo API 语义下,绑骨任务自身的 `task_id` 与"已绑骨模型"标识一致,链直接透传)。
- **每跳成功就地刷新** `companion_models.provider_task_id` / `download_urls_json`,保证 `companion.model.retryDownload` 永远从"最近一次成功产物"的 `task_id` 重新 query 下载,符合 [PROTOCOL.md §1.2](../PROTOCOL.md) "绝不重新计费"契约。
- **云端产物即终产物**:链终 GLB 直接落盘,后端不做任何 GLB 内省或二次处理。
- **morph 由供应商产物自带**:链不注入、不兜底、不补缺。

## 2. 能力声明

定义在 `backend/services/image_to_3d/base.py`:

| ClassVar | 含义 |
|----------|------|
| `SUPPORTS_RIGGING` | 是否云端绑骨(提交后产生独立 rig task 与新 `task_id`) |
| `SUPPORTS_ANIMATE_BIND` | 是否云端动画绑定(接已绑骨模型的 `task_id`,产物含动画) |
| `SUPPORTS_MULTIVIEW` | 是否接受多视图种子图 |
| `SUPPORTS_NEGATIVE_PROMPT` | 是否接受 negative prompt |

接入一个供应商:在 `services/image_to_3d/providers/<name>/__init__.py` 继承 `ImageTo3DProvider`、在子类上覆写 ClassVar 与 `submit_image_to_model` / `poll` / `download` 抽象方法,可选覆写 `start_rig` / `start_animate_bind`(不实现抛 `NotImplementedError` 也算"不支持")。

**能力缺位自动跳过**:`SUPPORTS_RIGGING=False` 时链跳过 cloud_rig 跳直接进 cloud_animate_bind(或直接落盘);`SUPPORTS_ANIMATE_BIND=False` 时跳过 animate_bind 跳直接落盘当前 GLB。

## 3. 失败语义

`pipeline.run_capability_chain` 落以下分类(reason 均为客户端文案):

| reason 短码 | 含义 | retry_download |
|------------|------|----------------|
| `provider_failed` / `generation_failed` | 供应商 API 报错 / 提交 / poll 阶段失败 | False |
| `download_failed_retryable` | 下载环节失败(付费结果仍在,客户端可走重试下载路径) | True |
| `provider_unconfigured` | 未配置供应商 | False |

`retry_download=true` 触发客户端的「重试下载」入口([PROTOCOL.md §1.2](../PROTOCOL.md))。

## 4. Hunyuan 当前形态

Hunyuan 当前无 `SUPPORTS_RIGGING` / `SUPPORTS_ANIMATE_BIND`。链形如下:

```
submit(seed) → task_id_a
poll → ready → download → 落盘
```

即产出供应商原始 GLB,不做绑骨、不做动画绑定。客户端可消费(显示静态模型),但无法驱动骨骼动画。是否在 UI 入口(`list_providers()`)标注 `disabled_reason` 由产品侧决定——后端**不再因能力缺口而 fail**。

**供应商间桥(X 借道 Y)当前不实现**——`ImageTo3DProvider` 留有扩展位,本轮不引入;保留"用户选谁出谁"硬约束。

## 5. in-process 调度(web 直接驱动)

长任务机制:`pipeline._launch_pipeline_task` 用 `asyncio.create_task(...)` 在 web 进程同事件循环里跑;句柄存在 `pipeline._inflight_tasks` 模块级 dict。Task 用 `try/except BaseException + finally` 防御异常被吞,失败时把行 mark `failed`。

进程崩溃接续:`main.py:_resume_inflight_pipelines()` 在启动时扫 `status IN IN_FLIGHT_STATUSES` 的行,以 `retry_only=True` 重新接续(直接复用持久化的 `provider_task_id` query+download+chain),等价于进程替换。
