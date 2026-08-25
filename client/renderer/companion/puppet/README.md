# Puppet 渲染模块

2D 形象的高质量渲染路径：消费 see-through 产出的分层 PSD（22 语义层，含遮挡补全），在浏览器内自动装配并驱动。底座自 [Anime2.5DRig](https://github.com/852wa/Anime2.5DRig)（MIT）移植，后续按 PuppetLoom（AGPL，只借机制不搬代码）的规格逐步升级网格与绑定（见仓库记忆与总体计划）。

## 模块结构

| 文件 | 职责 |
|---|---|
| `vendor/` | 上游原样的 UMD 三件套（rigger.js 装配 / ag-psd.min.js PSD 解析 / genericparts.js 内置闭眼闭嘴差分）+ LICENSE，不进 lint/format |
| `vendor-loader.ts` | `?url` 导入产哈希资产，运行时注入经典 script 并等待就绪（vite 不处理 html 里的裸 `<script src>`） |
| `puppet-types.ts` | vendor 全局（`window.Rigger` 等）与 rig 结构的 TS 边界契约 |
| `artmesh.ts` | alpha 轮廓三角剖分：边界+内部采样 → 增量 Delaunay（Bowyer-Watson）→ 域外三角形按质心/边中点 alpha 剔除（Phase 2，替代规则网格） |
| `head-cage.ts` | 脸面/头骨双表面三角控制笼：左右颊+颅顶语义控制点、重心坐标、深度混合 μ（Phase 2）；双表面半径 Rf/Rs/rv + 六点脸面深度曲线（Phase 3） |
| `puppet-runtime.ts` | WebGL 运行时：每层 ArtMesh + deform 顶点形变（头转控制笼/呼吸/眨眼差分/发束弹簧/胸物理）+ 模板眼裁切；参数经 `target`/`auto` 字段注入 |
| `PuppetCanvas.tsx` | React 挂载壳，imperative handle 暴露 `loadPsd(buffer)` |

## 关键契约与设计

- **PSD 层命名**是装配的输入契约（face / eyewhite / irides / eyelash / eye_close / eyebrow / mouth_open / mouth_close / nose / ears / neck / topwear / bottomwear / handwear / front hair_N / back hair），上游 see-through 的产出逐字吻合；`rigger.normName` 内置少量别名归一（`mouth`→`mouth_open` 等）。
- **形变数学与上游逐字一致**（depth 视差、发束权重、弹簧参数）；外围（GL 装配、rAF 生命周期、参数注入、TS 类型）与本仓动画自动化层为本仓代码。升级机制时以"同种子同参数输出可复现"为回归基线。
- **ArtMesh（Phase 2）**：每层按 alpha 轮廓采样三角剖分（当前 23 层全 ArtMesh，约 4.2k 顶点/7.6k 三角形），顶点密度贴形（发梢/睫毛/下巴缘），退化层回退 quad。域外三角形靠 alpha discard 遮蔽，无需完整 CDT 的约束恢复。
- **头部控制笼（Phase 2）**：左右颊+颅顶三角笼，每顶点预计算重心权重 cb 与有效深度 dEff（脸面↔头骨混合；μ_base 复现原层深度保证回归基线，前发额外根随脸/梢随颅斜坡）。deform 头部块 = 控制点位移 + 重心混合——当前位移场仿射，与原公式精确一致；Phase 3 的深度曲线/远眼收窄只改控制点位移项。
- **伪 3D 转头（Phase 3，机制取自 PuppetLoom）**：头/颈走圆投影分支——角度参数即归一化正弦（θ = asin(a·sinθmax)，中心位移对参数线性、与 Phase 2 同幅），横向位移乘可见度轮廓 T=√(1-hx²)（hx 按逐顶点混合表面半径归一：μ=1 脸面 Rf → μ=0 头骨 Rs），远/近缘压缩项按真实余弦把两侧拉向轴心（远缘发层滑盖向脸 = 侧发贴脸缘，近缘转回身后）。六点脸面深度曲线（额/鼻梁/鼻尖/上下唇/下巴锚点，按 μ 仅作用于脸面表面）让鼻口等靠前点转/俯时移动更多。远眼收窄 = 对侧眼向眼心水平压缩 16%（纯几何，不动透明度）；周边可见度 = 远端侧挂件（耳等，眼/眉除外）随转角淡出。颈双隶属（上 0.85 跟头 → 下 0.12 跟领）+ 上身同源跟随（tick 内直接读平滑后头部偏航 ×0.24 进 body，不经第二套慢响应）。中立姿态几何严格恒等（所有新项为零）；身体组保持 Phase 2 线性场。
- **次级运动（Phase 4，机制取自 PuppetLoom）**：发束双弹簧 → 4 节点弹簧链（根节点硬跟头/身 60/40 混合位移 + 风动，下游节点逐节追踪父节点、刚度/阻尼沿链递减——自由端逐步获得惯性）；渲染按发束进度在链上取样、相对根节点的偏差即次级运动。裙双频 = bottomwear 腰线固定、双频正弦时间线左右摆（受 idle 门控）。耳事件 = 种子化偶发（14~30s）快速抬落约 4 次严格回中立；呆毛 = 前发顶部窄条检测（<35% 主发宽）+ 纵向弹簧，偶发事件激发弹动。自主漫游 = 种子化观察段落（`autoSeed` + mulberry32，~15s 环：左右观察→抬头→低头、动作间回正，无每帧随机数；耳/呆毛事件同受 `auto.rand` 门控保证姿态定格确定性）。眨眼/说话/微扫视仍为 Math.random 事件调度。
- **13 姿态安全验证与三级降级（Phase 5）**：`poseSafety()` 按当前参数重算 deform 后检查全部三角形的有向面积翻转（基准面积 <6px² 的边界退化细条不计——亚像素翻转不可见）与最大边拉伸比；可见度轮廓 T 换有界缘斜率剖面（缘部保底 40% 位移，斜率上界 (1-m²)/m），保证压缩项叠加下局部映射单调不折叠。`assessTier` 按 PSD 语义完整度分级：semantic（全语义层+眼锚点+发束链，全机制）/ grouped（整体运动 + 头转缩幅 0.75，远眼收窄等特征级机制关闭）/ minimal（仅整体呼吸/重心横移/倾斜，deform 逐顶点早退）。`frozen` 冻结呼吸相位与事件调度，姿态定格逐位可复现。调试页 `?poses=1`：冻结+关自动化后按 PuppetLoom 动作缩放阶梯（1→0.85→…→0.25）取首个全姿态零翻转档，header 写 `POSES_OK n=13 tier=… scale=… flips=… stretch=… first=[…最差层归因]`。内置 PSD 实测 semantic 档 scale=0.85 全绿（stretch 1.22；scale=1 仅剩 back hair 一处 18px² 翻转），13 姿态前景覆盖率带 [0.1040,0.1063]，运行时自主段落幅度上限 0.7 在安全包络内。
- **see-through 侧名补丁**：see-through 产出 `-l/-r` 后缀层名（eyewhite-l 等），绕过 vendor SLOTS 匹配导致 side/fade/眼锚点缺失（虹膜移动/眉毛/远眼收窄/耳淡出全部失效）——`patchSideParts` 在装配边界补齐 eyeL/eyeR 锚点，buildGlPart 规范 bn/side 并给开眼层补 fade。
- **动画自动化层（Phase 1）**：非对称呼吸（3.4s 周期，每 18~38s 一次深呼吸）、眨眼曲线（0.34s 全程、20% 半眨、16% 连眨）、视线跟随（`setGaze`，眼先动头跟随、3s 无更新过期回落漫游）、微扫视（指数衰减的小幅快速眼动）、说话合成（每句独立振幅 + 音素级 mouthForm 目标）。参数平滑按语义分速率（`PARAM_RATE`：眼 20~22 / 头 7 / 身 5）。
- **模拟/渲染解耦**：`advanceSim(seconds)` 以固定 1/60 步进接管内部时钟（rAF 退化为纯渲染），供无头验证与回归做确定性断言——Phase 5 十三姿态安全验证以此为地基；`snapshot()` 暴露平滑后参数只读快照，`forceBlink()` 为确定性眨眼钩子。
- **差分合成**：PSD 缺 eye_close / mouth_close 时用内置 genericparts 自动合成并染色适配（上游行为，保留）。
- **数据来源**：Phase 6 将从 `companion.2d.ready` 事件的 `kind=psd` manifest 分流到此模块（PSD 经签名 URL 拉取）；当前经 `puppet.html` 调试台手工驱动。

## 调试台

`client/puppet.html`（vite 入口 `puppet`）：拖入/选择 PSD 或"载入内置测试 PSD"（`client/assets/seethrough_output.psd`，本地文件、不入库）→ 自动装配 → 待机动画；画布区移动鼠标可体验视线跟随（虹膜+头部+上身），右侧滑杆直写 runtime 参数、复选框切换自动化开关（待机/漫游/眨眼/说话/视线）。`?autotest=1` 挂载后自动装载并经 `advanceSim` 跑确定性动画探针，结果写进 header（`AUTOTEST_OK parts=N warnings=M blink/mouth/breath/idle/gaze/mesh/chain/skirt=1 meshstat=A/Lam Vv Tt bns=<层名表>`），供无头浏览器 `--dump-dom` 断言（驱动：`client/.claude/skills/run-puppet-debug/`，本地不入库）。`?pose=ax,ay,az` 姿态定格（关自动化+冻结呼吸，直写角度并推进到稳态，header 附 `body=` 同源跟随值）；`?poses=1` 13 姿态安全验证（缩放阶梯+逐层归因，见上）。供头转扫掠与 Phase 5 姿态安全验证截图。

## 已知限制（按 Phase 计划消化）

- 与 SpriteStage / drivers / hitmap / 手势 / VFX 的集成在 Phase 6
- 说话动画目前是纯合成节奏（Phase 6 接 TTS/驱动层后按实际音素驱动）
- 尾巴/头饰圆弧摆动机制待有对应部件的模型接入（当前测试 PSD 无 tail/headwear 弹性层）
- 13 姿态在 scale=1 满幅仍有 back hair 一处 18px² 三角形翻转（0.85 首个全绿档）；运行时自主/视线驱动幅度上限 0.7 在安全包络内，Phase 6 drivers 接入时按 scale 报告约束幅度
