# Puppet 渲染模块（Phase 0c 底座）

2D 形象的高质量渲染路径：消费 see-through 产出的分层 PSD（22 语义层，含遮挡补全），在浏览器内自动装配并驱动。底座自 [Anime2.5DRig](https://github.com/852wa/Anime2.5DRig)（MIT）移植，后续按 PuppetLoom（AGPL，只借机制不搬代码）的规格逐步升级网格与绑定（见仓库记忆与总体计划）。

## 模块结构

| 文件 | 职责 |
|---|---|
| `vendor/` | 上游原样的 UMD 三件套（rigger.js 装配 / ag-psd.min.js PSD 解析 / genericparts.js 内置闭眼闭嘴差分）+ LICENSE，不进 lint/format |
| `vendor-loader.ts` | `?url` 导入产哈希资产，运行时注入经典 script 并等待就绪（vite 不处理 html 里的裸 `<script src>`） |
| `puppet-types.ts` | vendor 全局（`window.Rigger` 等）与 rig 结构的 TS 边界契约 |
| `puppet-runtime.ts` | WebGL 运行时：每层规则网格 mesh + deform 顶点形变（头转/呼吸/眨眼差分/发束弹簧/胸物理）+ 模板眼裁切；参数经 `target`/`auto` 字段注入 |
| `PuppetCanvas.tsx` | React 挂载壳，imperative handle 暴露 `loadPsd(buffer)` |

## 关键契约与设计

- **PSD 层命名**是装配的输入契约（face / eyewhite / irides / eyelash / eye_close / eyebrow / mouth_open / mouth_close / nose / ears / neck / topwear / bottomwear / handwear / front hair_N / back hair），上游 see-through 的产出逐字吻合；`rigger.normName` 内置少量别名归一（`mouth`→`mouth_open` 等）。
- **形变数学与上游逐字一致**（depth 视差、发束权重、弹簧参数）；外围（GL 装配、rAF 生命周期、参数注入、TS 类型）为本仓重写。升级机制时以"同种子同参数输出可复现"为回归基线。
- **差分合成**：PSD 缺 eye_close / mouth_close 时用内置 genericparts 自动合成并染色适配（上游行为，保留）。
- **数据来源**：Phase 6 将从 `companion.2d.ready` 事件的 `kind=psd` manifest 分流到此模块（PSD 经签名 URL 拉取）；当前经 `puppet.html` 调试台手工驱动。

## 调试台

`client/puppet.html`（vite 入口 `puppet`）：拖入/选择 PSD 或"载入内置测试 PSD"（`client/assets/seethrough_output.psd`，本地文件、不入库）→ 自动装配 → 待机动画；右侧滑杆直写 runtime 参数。`?autotest=1` 挂载后自动装载并把结果写进 header（`AUTOTEST_OK parts=N warnings=M`），供无头浏览器 `--dump-dom` 断言。

## 已知限制（按 Phase 计划消化）

- 网格是每层规则网格（Phase 2 换 alpha 轮廓三角剖分 ArtMesh + 语义控制笼）
- 头转是深度视差近似（Phase 3 换深度曲线 + 远眼收窄 + 周边可见度）
- 发物理是每束双弹簧（Phase 4 换多段链 + 确定性种子化自主段落）
- 与 SpriteStage / drivers / hitmap / 手势 / VFX 的集成在 Phase 6
