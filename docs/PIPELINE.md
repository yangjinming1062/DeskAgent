# 模型生成能力链（3D 与 2D 分层动画）

> 读者：后端 pipeline 维护者、新供应商接入方、客户端 2D / 3D 渲染引擎维护者。
>
> 本文档是 3D 生成链路与 2D 分层动画（Mesh2D）生成链路的唯一权威：种子图编排、供应商能力、链拓扑、失败语义、产物契约与客户端兑现策略只在这里展开。产品流程见 [DESIGN.md](../DESIGN.md)，跨模块接口见 [PROTOCOL.md](../PROTOCOL.md)，后端实现取舍见 [backend/README.md](../backend/README.md)。

## 1. 3D 链拓扑

```
submit(seed) → poll → raw task
[SUPPORTS_RIGGING] → start_rig → poll → rigged task
[SUPPORTS_ANIMATE_BIND & animation_clips(rig_type) ≠ {}] → start_animate_bind → poll → animated task
chain-end task → download → final GLB → companion-models/<uid>/<sha>.glb
```

**种子图编排**：

- 正面/背面全身图确认后，后端按供应商多视角能力准备模型输入：支持多视角时，用户在引导流程中可预览、微调重绘并确认背面立绘（确认时若已包含背面图则复用，与正面图一起按双视图（front + back）提交）；不支持多视角时跳过背面确认，仅提交正面图。
- 同一画风的背面种子图在正面微调重绘时复用；换画风确认时重绘背面种子图，避免不同画风混用。

**关键不变量**：

- **任务 id 串联**：每跳用前跳的任务 id 作为输入，链不传递 GLB 二进制；链末产物只下载一次并落盘。
- **能力缺位自动跳过**：无云端绑骨能力或绑骨失败时交付生模阶段的 GLB；只有绑骨成功且支持动画绑定、骨架具有预设动作时才进入动画绑定，否则交付已绑骨任务的 GLB（avian 即此情况）。
- **每跳成功就地刷新任务 id 与下载地址**：重试下载与进程崩溃接续都从已持久化的任务恢复，**绝不重新计费**。
- **提交后立即持久化任务句柄**（轮询前）：进程在轮询中崩溃时任务 id 不丢失，避免已付费但重启后被当作无状态重发的重复计费。
- **阶段标记标识当前任务在链上的位置**（submit / rig / animate）：崩溃接续据此判断产物是否为最终含动画的 GLB，并按序续跑后续跳。
- **动作映射只在动画绑定真正成功时持久化**：空字典代表该骨架不产出动画（avian 或绑定失败）；客户端拿到非空映射会去 GLB 里兑现动作，空映射是契约硬面。

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

**进程崩溃接续**（启动时自驱接续）：

- 动画绑定阶段 + 任务 id 存在 → 仅下载（链末产物已含动画）
- 绑骨阶段 + 任务 id 存在 → 续跑动画绑定与下载（绑骨已付费，不重发）
- 提交阶段 + 任务 id 存在 → 续跑绑骨、动画绑定与下载（生模已付费，不重发）
- 任一阶段若任务 id 缺失 → 安全侧判定生成失败并允许用户重生成

**模型记录与激活状态**：每次新生成请求创建独立记录行并解除旧记录激活态；新产物就绪后原子置为当前唯一生效模型。非强制生成请求自动复用已生效模型，避免重复调用。

**失败重发的真实状态探针**：本地判为失败但记录持有任务 id 时，下次发起生成请求先向供应商查询真实任务状态——三态决策：
- 供应商任务已成功 → 置为待下载并由流程自驱续跑（避免重复计费）
- 供应商任务确认失败、取消或封禁 → 允许创建新记录重新提交（供应商失败不计费）
- 网络异常、任务排队中或状态未知 → 保持现有状态且不重发提交，杜绝盲目重发带来的重复计费风险。

本地异常退出不代表供应商任务失败：只有明确确认失败才允许重发；无法确认时保持现状供排查。

## 4. 3D 产物契约（Tripo3D `spec=tripo`）

`spec=tripo` 是当前唯一可用的骨骼命名规范——`mixamo` 命名不被云端动画绑定端点接受（实测 `error_code 1004`）。

**命名约定**：零关节前缀（`Hips` / `Root` 视 rig 而定）；`L_` / `R_` 左右前缀；`*Twist01` / `*Twist02` 是蒙皮辅助骨，动画 track 不要直接引用；无 Eye / Jaw 节点（面部情绪走聊天窗头像）。

**绑骨算法版本与 preset 命名空间正交**：`spec` 是骨骼命名，`model` 是绑骨算法版本。biped 走 `v1.0-20240301` 解锁 90+ 个 `preset:biped:*` 预设库；其余 6 类走 `v2.5-20260210`。

**预设 token 表**（每条单独计费，故只绑产品必需最小集；biped 还受 Tripo `retarget` 单次 ≤ 5 动画的硬限制约束——超出返回 `code=1004 "animations size must be <= 5"`）：

| rig_type | 预设 token |
|---|---|
| biped | `idle` / `walk` / `laugh_01` / `sob` |
| quadruped / hexapod / octopod | 该骨架唯一预设（`preset:<rig>:walk`） |
| serpentine / aquatic | 该骨架唯一预设（`preset:<rig>:march`） |
| avian | **空**——hop 整跳跳过 |

## 5. 3D 客户端消费

**语义键收敛**（LLM 不可请求 = 状态 / 交互反馈类，共 2 个）：`idle` / `emotional`。LLM 可请求的键 = biped 的 `walk` / `laugh` / `cry`（3 个，biped 独有；非 biped 的 LLM 清单为空）。后端组装提示词时只把 LLM 可请求的键注入清单——LLM 永远无法命名一个客户端无法兑现的动作。

**映射下发路径**：`provider.animation_clips(rig_type)` 是声明式权威；落库到 `clip_map_json`；随 `model.ready` 事件 + `GET /api/companion/model` 响应一起下发。客户端**不持有任何供应商命名**。

**客户端兑现**（`AnimationMap.ts::resolveClip`）：供应商不承诺写进 GLB 的 clip 名与提交的 token 逐字一致，按三级降级——精确候选 → 叶名精确 → 叶名子串，大小写不敏感。

**缺失键兜底**：

- 状态 / 交互反馈类键缺席 → 客户端回退 `idle`，符合"永不空白"
- LLM 可请求类键缺席 → 客户端兑现落空停在绑定姿势；LLM 端因清单里没有该键而无法误请求

## 6. 2D 分层动画能力链（Mesh2D）

**链拓扑**：
```
fullbody_url → Vision LLM (6 部件 BBox 检测) → CPU 抠图裁切 → 遮挡边缘补全
             → Vision LLM (19 关键点估计) → 解剖学平滑 → 骨骼与 Mesh 装配 → manifest.json
```

**产物契约**（`manifest.json` 与部件切片 PNG）：
- `canvas`: `{"w": 1024, "h": 1366}` 画布基准。
- `skeleton.bones`: 19 骨骼层级拓扑（root → body_main → neck → head → ...）。
- `meshes`: 各部件图层定义（包含 `texture`、`geometry_w`、`geometry_h`、`origin: [cx, cy]`、`z_order` 以及 `bones_influences` 权重）。
- `animations`: 包含 `breath`（呼吸振幅与周期）、`blink`（眨眼周期与时长）、`idle_sway`（摇摆幅度）、`jiggle`（发丝/裙摆物理弹簧 $k$ 与阻尼 $c$）。

**客户端 2D 运行时兑现**（`client/renderer/companion/mesh2d/mesh2d-runtime.ts`）：
- 采用 Three.js OrthographicCamera + SkinnedMesh，各部件 Mesh 经 `geometry.translate(origin.x, -origin.y)` 精准装配。
- 骨骼层级计算局部相对偏移（`child.position = childAbsPos - parentAbsPos`）。
- 运行时逐帧计算呼吸、视线跟随（Yaw / Pitch）、眨眼 Ease、TTS 口型振幅以及 Verlet/弹簧 Jiggle 物理。

## 7. 渲染与传输

- **3D 客户端**：纯 GLB 播放渲染引擎——动画全部来自 `gltf.animations`，无程序化注入；Gzip 透明解压与 OPFS 内容哈希缓存。
- **2D 客户端**：Three.js SkinnedMesh 正交相机渲染，秒级就绪且零额外 GPU 负担。

## 8. 验证 checklist

- [ ] clip track 引用的 bone name 与 `spec=tripo` 对应 rig 的层级一致
- [ ] biped 颈段取 `NeckTwist01` 兜底（`spec=tripo` 无 `Neck` 节点）
- [ ] 2D `manifest.json` 各 mesh 均包含正确的 `origin` 画布中心坐标
- [ ] 客户端兑现按三级降级落空时回退到绑定姿势而非抛错

## 9. 参考实现

- 3D 能力链编排：`backend/services/companion/pipeline.py::run_capability_chain`
- 2D 切分编排：`backend/services/companion/mesh2d/pipeline.py::run_mesh2d_pipeline`
- 3D 客户端兑现：`client/renderer/companion/3d/AnimationMap.ts`
- 2D 客户端运行时：`client/renderer/companion/mesh2d/mesh2d-runtime.ts`
