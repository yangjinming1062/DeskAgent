# 3D 模型生成能力链

> 读者：后端 pipeline 维护者、新供应商接入方、客户端 3D 引擎维护者。
>
> 本文档描述链拓扑、不变量、产物契约、客户端消费策略；具体代码位置与函数路径见 §8 参考实现。

## 1. 链拓扑

```
submit(seed) → poll → raw task
[SUPPORTS_RIGGING] → start_rig → poll → rigged task
[SUPPORTS_ANIMATE_BIND & animation_clips(rig_type) ≠ {}] → start_animate_bind → poll → animated task
chain-end task → download → final GLB → companion-models/<uid>/<sha>.glb
```

**关键不变量**：

- **`task_id` 串联**：每跳用前跳的 `task_id` 作为输入，链不传递 GLB bytes；链末产物只下载一次并落盘。
- **能力缺位自动跳过**：`SUPPORTS_RIGGING=False` 或绑骨失败时交付 raw task 的 GLB；只有绑骨成功且 `SUPPORTS_ANIMATE_BIND=True`、`animation_clips(rig_type) != {}` 时才进入 animate_bind，否则交付已绑骨 task 的 GLB（avian 即此情况）。
- **每跳成功就地刷新 `provider_task_id` + `download_urls_json`**：重试下载 / 进程崩溃接续都从这里恢复，**绝不重新计费**。
- **`provider_phase` 标识当前 task_id 在链上的阶段**（submit / rig / animate）：崩溃接续据此判断产物是不是最终含动画的 GLB。
- **`clip_map_json` 只在 animate_bind 真正成功时落**：`{}` 代表该骨架不产出动画（avian 或绑定失败）；客户端拿到非空映射会去 GLB 里兑现 clip，空映射是契约硬面。

## 2. 能力声明

定义在 `ImageTo3DProvider`（`backend/services/image_to_3d/base.py`）：

| ClassVar / 方法 | 含义 |
|---|---|
| `SUPPORTS_RIGGING` | 是否云端绑骨 |
| `SUPPORTS_ANIMATE_BIND` | 是否云端动画绑定 |
| `animation_clips(rig_type) -> dict` | 该骨架的「语义键 → 预设 token」映射；空字典 = 不产出动画 |
| `SUPPORTS_MULTIVIEW` | 提交方法是否按多图模式消费种子图；关闭时编排侧只提交正面图 |
| `SUPPORTS_NEGATIVE_PROMPT` | 是否接受 negative prompt |

接入新供应商：继承 `ImageTo3DProvider`、覆写 ClassVar 与三个抽象方法（`create_image_to_model` / `poll` / `download`），可选覆写 `start_rig` / `start_animate_bind` / `animation_clips`（默认实现抛 `ImageTo3DError` 等价于"不支持"）。

## 3. 失败与重试语义

| reason 短码 | 含义 | retry_download |
|---|---|---|
| `generation_failed` | 供应商 API / submit / poll 失败 | False |
| `download_failed_retryable` | 下载环节失败，付费结果仍在 | True |
| `provider_unconfigured` | 未配置供应商 | False |

**进程崩溃接续**（`_resume_inflight_pipelines`）：

- `provider_phase = "animate"` → 链已完整，`retry_only=True` 跳过 submit + 增强跳直接落盘
- `provider_phase ∈ {submit, rig}` → 链未完成到 animate（中间产物无动画）→ mark `download_failed` 让用户重生成；`retry_download=false`，因为重试下载也救不回来

**`retry_only=True` 跳过增强跳**：`provider_task_id` 已是链末任务，再喂回 `start_rig` 会让供应商报错并重复计费。映射此时从 `clip_map_json` 直接读回。

## 4. 产物契约（Tripo3D `spec=tripo`）

`spec=tripo` 是当前唯一可用的骨骼命名规范——`mixamo` 命名不被云端动画绑定端点接受（实测 `error_code 1004`）。

**命名约定**：零关节前缀（`Hips` / `Root` 视 rig 而定）；`L_` / `R_` 左右前缀；`*Twist01` / `*Twist02` 是蒙皮辅助骨，动画 track 不要直接引用；无 Eye / Jaw 节点（面部情绪走聊天窗头像）。

**绑骨算法版本与 preset 命名空间正交**：`spec` 是骨骼命名，`model` 是绑骨算法版本。biped 走 `v1.0-20240301` 解锁 90+ 个 `preset:biped:*` 预设库；其余 6 类走 `v2.5-20260210`。

**预设 token 表**（每条单独计费，故只绑产品必需最小集）：

| rig_type | 预设 token |
|---|---|
| biped | `idle` / `walk` / `laugh_01` / `jump` / `greet_01` / `sob`（10 个语义键收敛到这 6 条） |
| quadruped / hexapod / octopod | 该骨架唯一预设（`preset:<rig>:walk`） |
| serpentine / aquatic | 该骨架唯一预设（`preset:<rig>:march`） |
| avian | **空**——hop 整跳跳过 |

## 5. 客户端消费

**语义键收敛**（LLM 不可请求 = 状态 / 交互反馈类，共 5 个）：`idle` / `emotional` / `interacting` / `poke` / `drag`。LLM 可请求的键 = biped 的 `walk` / `jump` / `laugh` / `greet` / `cry`（5 个，biped 独有；非 biped 的 LLM 清单为空）。后端组装提示词时只把 LLM 可请求的键注入清单——LLM 永远无法命名一个客户端无法兑现的动作。

**映射下发路径**：`provider.animation_clips(rig_type)` 是声明式权威；落库到 `clip_map_json`；随 `model.ready` 事件 + `GET /api/companion/model` 响应一起下发。客户端**不持有任何供应商命名**。

**客户端兑现**（`AnimationMap.ts::resolveClip`）：供应商不承诺写进 GLB 的 clip 名与提交的 token 逐字一致，按三级降级——精确候选 → 叶名精确 → 叶名子串，大小写不敏感。

**缺失键兜底**：

- 状态 / 交互反馈类键缺席 → 客户端回退 `idle`，符合"永不空白"
- LLM 可请求类键缺席 → 客户端兑现落空停在绑定姿势；LLM 端因清单里没有该键而无法误请求

## 6. 渲染与传输

客户端纯 GLB 播放渲染引擎——动画全部来自 `gltf.animations`，无程序化注入。

- **风格分流**：类人面孔走二次元画风（避免恐怖谷），非人生物走写实路线；GLB 客户端统一以 PBR 渲染
- **Gzip 透明解压**：供应商原始 GLB 经 Gzip 压缩（蒙皮权重等稀疏数据占大头，缩至 ~10%），客户端 `DecompressionStream` 自动识别魔数并透明还原
- **整文件缓存**：下载到 OPFS 后按 `content_hash` 复用，重复加载瞬时完成

## 7. 验证 checklist

- [ ] clip track 引用的 bone name 与 `spec=tripo` 对应 rig 的层级一致
- [ ] biped 颈段取 `NeckTwist01` 兜底（`spec=tripo` 无 `Neck` 节点）
- [ ] 没有 clip track 引用 `*Twist01` / `*Twist02` 等蒙皮辅助骨
- [ ] 客户端兑现按三级降级落空时回退到绑定姿势而非抛错

## 8. 参考实现

- 能力链编排：`backend/services/companion/pipeline.py::run_capability_chain`
- 能力声明基类：`backend/services/image_to_3d/base.py::ImageTo3DProvider`
- Tripo 客户端（rig / retarget / preset 表）：`backend/services/image_to_3d/providers/tripo/client.py`
- 客户端兑现：`client/renderer/companion/3d/AnimationMap.ts`
- 状态机与头/颈注视叠加：`client/renderer/companion/3d/CharacterController.ts`
