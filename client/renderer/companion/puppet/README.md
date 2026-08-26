# Puppet 渲染模块

2D 形象的高保真渲染路径：消费 see-through 产出的分层 PSD（22 语义层，含遮挡补全），在浏览器内自动装配并驱动。底座自 [Anime2.5DRig](https://github.com/852wa/Anime2.5DRig)（MIT）移植；机制升级对标 PuppetLoom（AGPL-3.0）——**只学机制，不搬代码与文字**，规避许可证传染。

## 模块结构

| 文件 | 职责 |
|---|---|
| `vendor/` | 上游原样的 UMD 三件套（rigger.js 装配 / ag-psd.min.js PSD 解析 / genericparts.js 内置闭眼闭嘴差分）+ LICENSE，不进 lint/format |
| `vendor-loader.ts` | `?url` 导入产哈希资产，运行时注入经典 script 并等待就绪（vite 不处理 html 里的裸 `<script src>`） |
| `puppet-types.ts` | vendor 全局（`window.Rigger` 等）与 rig 结构的 TS 边界契约 |
| `artmesh.ts` | alpha 轮廓三角剖分：边界+内部采样 → 增量 Delaunay → 域外三角形按 alpha 剔除 |
| `head-cage.ts` | 脸面/头骨双表面三角控制笼：语义控制点、重心坐标、深度混合与六点脸面深度曲线 |
| `puppet-runtime.ts` | WebGL 运行时：每层 ArtMesh + 顶点形变（伪 3D 转头 / 呼吸 / 眨眼 / 次级物理）+ 模板眼裁切；参数经 `target`/`auto` 字段注入 |
| `PuppetCanvas.tsx` | React 挂载壳，imperative handle 暴露 `loadPsd(buffer)` |
| `puppet-store.ts` | 生产数据源：拉 mesh2d 行 manifest 判 `kind=psd` → 暴露签名 PSD URL；非 psd（防御分支）/失败保持未就绪让级联落 3D/蛋 |
| `PuppetStage.tsx` | 生产挂载与驱动层：命中区域、视线、TTS 嘴型、情绪、动作包络、hover 发区冲量（见下） |

## 关键契约与设计

- **PSD 层命名**是装配的输入契约（face / eyewhite / irides / eyelash / eye_close / eyebrow / mouth_open / mouth_close / nose / ears / neck / topwear / bottomwear / legwear / handwear / front hair_N / back hair），上游 see-through 的产出逐字吻合；`rigger.normName` 内置少量别名归一（`mouth`→`mouth_open`、`leg_wear`→`bottomwear` 等）。**侧名补丁**：see-through 产出 `-l/-r` 后缀层名（eyewhite-l 等），绕过 vendor SLOTS 匹配导致侧别/淡出/眼锚点缺失（虹膜移动/眉毛/远眼收窄/耳淡出全部失效）——装配边界统一补齐眼锚点并规范层名、侧别与开眼层淡出标记。
- **头部五官权威图层层序排序（Head Canonical Z-Order）**：装配阶段仅针对头部五官与前发槽位（face < facedetail < mouth < eyewhite < irides < eyelash/eye_close < nose < eyebrow < front hair）在其占用的槽位间进行解剖学稳定重排，确保任意刘海/碎发自然覆盖眼眉并彻底解决「眼睛悬浮在头发上」问题；**身体、躯干、四肢、服装与配饰图层 100% 保留上游 PSD 的原始层级顺序**，杜绝手臂与裙服层级穿模。
- **形变数学与上游逐字一致**（depth 视差、发束权重、弹簧参数）；外围（GL 装配、rAF 生命周期、参数注入、TS 类型）与本仓动画自动化层为本仓代码。升级机制时以"同种子同参数输出可复现"为回归基线，中立姿态几何严格恒等。
- **ArtMesh**：每层按 alpha 轮廓采样三角剖分，顶点密度贴形（发梢/睫毛/下巴缘），退化层回退 quad；域外三角形靠 alpha discard 遮蔽，无需完整 CDT 的约束恢复。
- **头部控制笼**：左右颊+颅顶三角笼，每顶点预计算重心权重与有效深度（脸面↔头骨混合；前发根随脸、梢随颅）。deform 头部块 = 控制点位移 + 重心混合，位移场仿射。
- **伪 3D 转头**（机制取自 PuppetLoom）：头/颈走圆投影分支——角度参数为归一化正弦（中心位移对参数线性），横向位移乘有界缘斜率的可见度轮廓（缘部保底位移、斜率有上界，保证压缩项叠加下局部映射单调不折叠——纯圆根在缘部斜率无界会导致网格折叠），远/近缘压缩项按真实余弦把两侧拉向轴心（远缘发层滑盖向脸 = 侧发贴脸缘，近缘转回身后）。六点脸面深度曲线（额/鼻梁/鼻尖/上下唇/下巴锚点，仅作用于脸面表面）让鼻口等靠前点转/俯时移动更多。远眼收窄 = 对侧眼向眼心水平压缩（纯几何，不动透明度）；周边可见度 = 远端侧挂件（耳等，眼/眉除外）随转角淡出。颈上端跟头、下端跟领的双隶属 + 上身同源跟随（直接读平滑后头部偏航，不经第二套慢响应）。
- **次级运动**（机制取自 PuppetLoom）：发束为节点弹簧链（根节点硬跟头/身混合位移 + 风动，下游节点逐节追踪父节点、刚度/阻尼沿链递减——自由端逐步获得惯性）；渲染按发束进度在链上取样、相对根节点的偏差即次级运动。裙摆腰线固定、双频正弦左右摆（受 idle 门控）；耳为种子化偶发快速抬落、严格回中立；呆毛由前发顶部窄条检测 + 纵向弹簧、偶发事件激发弹动。自主漫游为**种子化观察段落**（种子 + mulberry32，十几秒环：左右观察→抬头→低头、动作间回正，无每帧随机数；耳/呆毛事件同受自动化开关门控保证姿态定格确定性）。
- **13 姿态安全验证与三级降级**：`poseSafety()` 按当前参数重算形变后检查全部三角形的有向面积翻转（亚像素级边界退化细条不计）与最大边拉伸比。`assessTier` 按 PSD 语义完整度分级：semantic（全语义层+眼锚点+发束链，全机制）/ grouped（整体运动 + 头转缩幅，远眼收窄等特征级机制关闭）/ minimal（仅整体呼吸/重心横移/倾斜，形变逐顶点早退）。`frozen` 冻结呼吸相位与事件调度，姿态定格逐位可复现。调试页 `?poses=1`：冻结+关自动化后按动作缩放阶梯（满幅→0.25）取首个全姿态零翻转档，header 写 `POSES_OK n=13 tier=… scale=… flips=… stretch=… first=[…最差层归因]`。
- **动画自动化层**：非对称呼吸（含偶发深呼吸）、眨眼曲线（全眨/半眨/连眨）、视线跟随（眼先动头跟随，无更新过期回落漫游）、微扫视（指数衰减的小幅快速眼动）、说话合成（每句独立振幅 + 音素级嘴型目标）。参数平滑按语义分速率（眼快、头身慢）。
- **模拟/渲染解耦**：`advanceSim(seconds)` 以固定步进接管内部时钟（rAF 退化为纯渲染），供无头验证与回归做确定性断言——姿态安全验证以此为地基；`snapshot()` 暴露平滑后参数只读快照，`forceBlink()` 为确定性眨眼钩子。
- **差分合成**：PSD 缺 eye_close / mouth_close 时用内置 genericparts 自动合成并染色适配（上游行为，保留）。
- **数据来源与渲染级联**：see-through 产出 `spiritagent.2d.psd/1` 描述符（`kind=psd`）复用 mesh2d 行与 WS 事件路径；`companion.2d.ready` / outfit 穿着 / 头像重生事件后 `hydratePuppet` 判 kind。root.tsx 渲染级联：**puppet（PSD）→ 3D → 程序化蛋**——puppet 装配失败写 error 熄灭 `$puppetReady` 自动落级，永不空白（DESIGN §1.2）。
- **驱动层映射**：视线 = 指针归一化注入 + `$gazeTarget` 显式目标周期续注（ritual walk / perch 锁定）；说话 = TTS 振幅接管嘴型并暂停合成说话、静默后交还；情绪 = 后端情绪词表全对齐 → 眉/嘴型/眼缩放参数（puppet 独有面部通道）；动作 = 动作白名单键 → 定时包络 + 队列续播；hover 发区 → 发束冲量（节流，方向随戳侧）。

## 调试台

`client/puppet.html`（vite 入口 `puppet`）：拖入/选择 PSD 或"载入内置测试 PSD"（`client/assets/seethrough_output.psd`，本地文件、不入库）→ 自动装配 → 待机动画；画布区移动鼠标可体验视线跟随（虹膜+头部+上身），右侧滑杆直写 runtime 参数、复选框切换自动化开关（待机/漫游/眨眼/说话/视线）。`?autotest=1` 挂载后自动装载并经 `advanceSim` 跑确定性动画探针，结果写进 header（`AUTOTEST_OK parts=N warnings=M blink/mouth/breath/idle/gaze/mesh/chain/skirt=1 meshstat=A/Lam Vv Tt bns=<层名表>`），供无头浏览器 `--dump-dom` 断言（驱动：`client/.claude/skills/run-puppet-debug/`，本地不入库）。`?pose=ax,ay,az` 姿态定格（关自动化+冻结呼吸，直写角度并推进到稳态，header 附 `body=` 同源跟随值）；`?poses=1` 13 姿态安全验证（缩放阶梯+逐层归因，见上）。`?stage=1` 无头验证生产挂载（真实 PuppetStage + 驱动层，注入内置 PSD 当签名 URL，header 写 `STAGE_OK regions=…`）。

## 已知限制

- 尾巴/头饰圆弧摆动机制待有对应部件的模型接入（当前测试 PSD 无 tail/headwear 弹性层）
- 13 姿态在满幅缩放仍有 back hair 一处小面积三角形翻转（降一档即全绿）；驱动层动作包络幅度按安全包络设计，姿态安全缩放报告尚未自动约束 LLM 动作幅度
- 说话嘴型按 TTS 振幅包络驱动（非音素级）；音素驱动留待 TTS 层暴露音素流
- 拖拽 locomotion 的姿态反馈（被拎起的空中姿态）尚未接 puppet
