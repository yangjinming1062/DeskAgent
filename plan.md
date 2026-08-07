# Client 整合计划：Desktop + 3D 渲染引擎合并

> **目标**：将 `client/` 的 Three.js 3D 渲染引擎整合进 `desktop/`，用实时 3D 渲染替换预渲染视频管线，最终将整合后的 desktop 重命名为 `client/` 作为唯一的桌面客户端。旧 `client/` 目录在整合完成后删除。

## 核心判断

整合不是"合并两个代码库"——而是 **在 desktop 现有 renderer 树内做一次外科手术式的渲染层替换**。

desktop 已经拥有完整的、经过实战验证的渲染基础设施：

- **`root.tsx`**：生命周期编排（unauthed-egg -> onboarding -> ready）、auth 监听、gateway boot 挂载
- **`sprite-stage.tsx`**：拖拽 / click-through / 交互区域 / 双击打开对话
- **`events.ts`**：后端事件 -> 状态机 + TTS 分发（message.* / tool.* / companion.* / clip.updated）
- **`tts.ts`**：TTS 播放（经 IPC `deskagent.media.tts`，非 client 的直接 REST）
- **`chat-store.ts` / `chat-dock.tsx`**：对话 UI
- **`companion-store.ts`**：状态机（优先级门控 + 瞬态语义）
- **`boot/use-gateway-boot.ts`**：WS 网关连接 + 重连退避 + sleep/wake 恢复
- **`JsonRpcGatewayClient`**：完整的 JSON-RPC 客户端（reconnect / auth ticket 轮换 / 超时管理）

client/ 的对应实现（`GatewayClient.ts` / `auth-store.ts` / `chat-store.ts` / `events.ts` / `tts.ts`）都是简化原型，**全部丢弃**。只移植引擎核心代码。

### 整合矩阵

| 层 | 来源 | 处理方式 |
|----|------|----------|
| 3D 引擎（Engine / LightingRig / CharacterController / MorphController / AnimationMap） | client/src/engine/ | **移植**到 `renderer/companion/3d/` |
| 渲染组件（CompanionReady 替换） | 新建 | 用 Three.js Canvas 组件替换 `companion-ready.tsx` |
| 资产目录（clip-store） | 新建 | 用 `model-store.ts` 替换 `clip-store.ts`（3D 模型 + 换装） |
| 状态机（companion-store） | desktop 保留 | 不动——client 的版本是从 desktop 移植的 |
| 事件分发（events.ts） | desktop 保留 | 改 clip.updated -> model.ready / wardrobe.updated |
| TTS（tts.ts） | desktop 保留 | 加 lip-sync 振幅回调（client 的 Web Audio 方案移植进来） |
| Gateway / auth / boot | desktop 保留 | 完全不动 |
| ChatDock / VoiceCallDock / Settings / Egg / Onboarding | desktop 保留 | 完全不动 |
| client/src/ui/* | 丢弃 | desktop 的 UI 更完整 |
| client/src/gateway/* | 丢弃 | desktop 的 `JsonRpcGatewayClient` 更健壮 |
| client/src/api.ts | 丢弃 | desktop 经 IPC `window.deskagent.api()` 调 REST |
| client/src/state/auth-store.ts / chat-store.ts / gateway-store.ts | 丢弃 | desktop 有对应实现 |

---

## Phase 1：引擎移植（desktop 内新增 3D 渲染层）

### 1.1 安装 Three.js 依赖

在 `desktop/package.json` 的 `devDependencies` 加：

```
"three": "^0.185",
"@types/three": "^0.185"
```

```bash
cd desktop && pnpm add -D three@^0.185 @types/three@^0.185
```

### 1.2 移植引擎核心

从 `client/src/engine/` 复制到 `desktop/renderer/companion/3d/`：

| 源文件 | 目标 | 改动 |
|--------|------|------|
| `engine/Engine.ts` | `3d/engine.ts` | 无 |
| `engine/LightingRig.ts` | `3d/lighting-rig.ts` | 无 |
| `engine/CharacterController.ts` | `3d/character-controller.ts` | 无 |
| `engine/MorphController.ts` | `3d/morph-controller.ts` | 无 |
| `engine/AnimationMap.ts` | `3d/animation-map.ts` | 无 |
| `engine/types.ts` | `3d/types.ts` | 无 |

import 路径改为 `@/companion/3d/...`（desktop 的 `@` alias 指向 `renderer/`）。

### 1.3 新建 `3d/model-store.ts`（替换 clip-store）

取代 `clip-store.ts` 的视频资产目录。管理 3D 模型 URL + 换装项：

```ts
interface ModelInfo {
  asset_url: string | null
  species: string | null
  has_rig: boolean
  has_morph_targets: boolean
}

interface WardrobeItem {
  id: number
  name: string
  material_overrides: Record<string, { color?: string; roughness?: number; metalness?: number }>
  texture_url: string | null
  equipped: boolean
}
```

事件对接：
- `model.ready {model_id, asset_url, species}` -> 更新 `$modelInfo` + 触发引擎加载
- `wardrobe.updated {}` -> 重新拉取 `GET /api/companion/wardrobe`

### 1.4 新建 `3d/companion-3d.tsx`（替换 companion-ready.tsx）

React 组件，将 Three.js 引擎挂载到 `<canvas>` 上，替换 `companion-ready.tsx` 的视频/sprite/portrait 三档渲染。

职责：
- 挂载 `<canvas>` 到 DOM（在 `SpriteStage` 的 children slot 内）
- 初始化 `Engine` 实例，渲染循环跑 `requestAnimationFrame`
- 订阅 `$spriteState` / `$spriteEmotion` -> 调 `characterController.applyState()`
- 加载 3D 模型：经 `window.deskagent.api({ path: '/api/companion/model' })` 获取 model URL -> `characterController.load(url)`
- 订阅 `model-store` 的 `$modelInfo` -> 模型就绪时加载
- TTS lip-sync：hook 进 desktop 的 `tts.ts`，用 Web Audio AnalyserNode 提取振幅 -> `characterController.setLipSyncAmplitude()`
- 微动作循环：复用 `companion-ready.tsx` 的 idle variant 定时器逻辑，但改为调 `characterController.playClip('idle_look_around')` 等

### 1.5 改 `tts.ts` 加 lip-sync

desktop 的 `tts.ts` 经 IPC 拿 TTS audio（data URL），通过 `audio-track.ts` 播放。需要加一个 Web Audio AnalyserNode 提取实时振幅：

- 在 `audio-track.ts` 创建一个共享 `AudioContext` + `AnalyserNode`
- `MediaElementSource` 连接 -> `AnalyserNode` -> `destination`
- 暴露 `getAudioAmplitude(): number` 供引擎每帧读取
- 播放开始/结束时通知引擎 enable/disable lip-sync

### 1.6 改 `events.ts` 加 model/wardrobe 事件

在 `handleCompanionEvent` 的 switch 加：

```ts
case 'model.ready': {
  const p = event.payload as { model_id?: number; asset_url?: string; species?: string; error?: string }
  updateModelInfo(p)
  break
}
case 'wardrobe.updated': {
  refreshWardrobe()
  break
}
```

删除 `case 'clip.updated'` 分支。

### 1.7 改 `root.tsx` 替换渲染组件

```diff
-import { CompanionReady } from './sprite/companion-ready'
+import { Companion3D } from './3d/companion-3d'
 ...
-{showReady ? <CompanionReady /> : ...}
+{showReady ? <Companion3D /> : ...}
```

### 1.8 删除旧视频资产层

删除 `renderer/companion/sprite/companion-ready.tsx`。
删除 `renderer/companion/clip-store.ts`。
保留 `sprite/sprite-stage.tsx`（窗口/拖拽/click-through 不变）。
保留 `sprite/egg.tsx` / `sprite/silhouette.tsx` / `sprite/context-menu.tsx`。

### 1.9 验证

- `pnpm type-check` 通过
- `pnpm dev` 启动 Electron，精灵窗口显示 3D 角色（或程序化兜底角色）
- 点击角色 -> 状态切换 -> 骨骼动画播放
- 发消息 -> thinking -> speaking + TTS 口型同步

---

## Phase 2：换装集成

### 2.1 换装 UI

在 `settings-overlay.tsx` 的"形象管理"区加换装面板：

- `GET /api/companion/wardrobe` 拉取已生成项
- `POST /api/companion/wardrobe` 生成新 AI 纹理
- `PUT /api/companion/wardrobe/{id}/equip` 装备
- 装备时调 `characterController.setOutfit(item)`

### 2.2 3D 模型触发生成

onboarding 完成后（或 portrait 确认后），调 `POST /api/companion/model` 触发 3D 模型生成。base_texture provider 即时返回 -> `model.ready` 事件推送 -> 引擎加载。

---

## Phase 3：模块重命名（desktop -> client）

Phase 1-2 验证通过后，将整合完 3D 引擎的 `desktop/` 重命名为 `client/`。

### 3.1 重命名

```bash
git mv client client-prototype
git mv desktop client
```

### 3.2 全局路径更新

需要更新的文件（所有引用 `desktop/` 路径的地方）：

| 文件 | 改动 |
|------|------|
| `AGENTS.md` | 模块表 `desktop/` -> `client/`，layout 描述 |
| `README.md` | 快速启动、构建命令中的 `desktop/` -> `client/` |
| `RULES.md` | 平台支持策略中的 `desktop/` -> `client/` |
| `ARCHITECTURE.md` | S2 拓扑图、S3 责任矩阵、S11 索引 |
| `COMPANION_DESIGN.md` | header 引用 `desktop/README.md` -> `client/README.md` |
| `scripts/build_client.ps1` / `.sh` | 路径 `desktop/` -> `client/` |
| `scripts/` 下所有引用 desktop 的脚本 | 全量搜索替换 |
| `installer/` 中引用 desktop 的路径 | 搜索 `desktop` |
| `client/` 内部 `vite.config.ts` / `tsconfig.json` / `package.json` | 无需改（相对路径不变） |
| `client/README.md` | 重写为最终客户端文档 |
| CI 配置（如有） | 搜索 `desktop` |

### 3.3 删除旧 client/ 原型

```bash
rm -rf client-prototype/
```

旧 client/src/ 的引擎代码已在 Phase 1 移植到新 client/renderer/companion/3d/，原型目录无保留价值。

---

## Phase 4：文档全面更新

### 4.1 消除所有"Client（3D 渲染引擎原型）"描述

当前文档中以下位置仍描述 client/ 为独立原型模块，整合后必须全部修正：

**ARCHITECTURE.md：**
- S11 索引中的 `Client（3D 渲染引擎原型）` 行 -> 删除（3D 引擎现在是 client 的一部分）
- S11 Desktop/Client 描述中的"3D 渲染引擎（移植自 client/）" -> 改为"3D 实时渲染引擎（Three.js WebGL）"，不再提"移植"
- S2 拓扑图中的 `Three.js + 透明置顶窗口` 描述 -> 保留，不再标注 Phase 5
- S3 责任矩阵 -> "3D 实时渲染桌面伙伴形象"

**COMPANION_DESIGN.md：**
- header 引用 `[desktop/README.md]` -> `[client/README.md]`
- S1.2 渲染约束中的 `核心代码在 desktop/renderer/companion/3d/` -> `核心代码在 client/renderer/companion/3d/`

**AGENTS.md：**
- 模块表删除 `Client (3D rendering prototype)` 行
- layout 描述删除 `client/` 原型条目
- Desktop 条目重命名为 Client

**README.md：**
- 模块描述更新
- 快速启动命令路径更新

**client/README.md（原 desktop/README.md -> 重写）：**
- 完全重写：不再描述"3D 引擎将移植进来"，而是描述完整的最终客户端架构
- 包含 3D 引擎章节（3d/ 目录结构）
- 标注旧 client/ 原型已废弃删除

**backend/README.md：**
- 移除"Phase 5 移植"相关描述
- 前端 API 消费方从"Desktop/Client"统一改为"Client"

**assets/base-models/README.md：**
- 输出路径 `backend/assets/base-models/` 保持不变（部署路径不受前端重命名影响）

### 4.2 全局检查清单

整合完成后，在仓库根目录执行以下搜索，确保零残留：

```powershell
# 搜索所有仍在引用旧 desktop/ 路径的文件
Get-ChildItem -Recurse -Include *.md,*.ps1,*.sh,*.cjs,*.mjs,*.json | Select-String "desktop/" | Where-Object { $_.Path -notmatch "node_modules|\.git" }

# 搜索所有仍在引用旧 client/ 原型的文件
Get-ChildItem -Recurse -Include *.md | Select-String "prototype|原型" | Where-Object { $_.Path -notmatch "node_modules|\.git" }

# 搜索残留的视频管线引用
Get-ChildItem -Recurse -Include *.md | Select-String "clip.updated|avatar.list_clips|companion-ready|clip-store" | Where-Object { $_.Path -notmatch "node_modules|\.git" }
```

以上每个搜索都应返回零结果（或仅历史 git log 中的匹配）。

---

## 执行顺序与依赖

```
Phase 1（引擎移植）
  |
  +- 1.1 安装 three 依赖
  +- 1.2 移植 6 个引擎文件
  +- 1.3 新建 model-store
  +- 1.4 新建 companion-3d.tsx
  +- 1.5 改 tts.ts 加 lip-sync
  +- 1.6 改 events.ts 加 model/wardrobe 事件
  +- 1.7 改 root.tsx 挂载 3D 组件
  +- 1.8 删除 companion-ready.tsx + clip-store.ts
  +- 1.9 验证 <- gate
         |
Phase 2（换装集成）
  |
  +- 2.1 换装 UI
  +- 2.2 onboarding 触发模型生成
         |
Phase 3（重命名 desktop -> client）
  |
  +- 3.1 git mv
  +- 3.2 全局路径更新
  +- 3.3 删除旧 client/
         |
Phase 4（文档全面更新）
  |
  +- 4.1 消除原型描述
  +- 4.2 全局检查清单 <- gate
```

Phase 1 是 gate——3D 渲染在 Electron 内跑通后才有意义做后续。Phase 3 的重命名是纯机械操作但涉及大量文件，需仔细。Phase 4 是收尾检查。

---

## 风险与注意事项

1. **Three.js 包体积**：three ~600KB minified，加上 desktop 现有的 bundle（~22MB），增量可接受。但需确认 electron-builder 打包不超限。
2. **WebGL 在透明窗口中的兼容性**：Electron 的 `transparent: true` BrowserWindow + WebGL canvas alpha 需要在 Windows 和 macOS 上都验证。Windows 的 DWM 合成可能有 quirks。
3. **`sprite-stage.tsx` 的交互区域**：当前 click-through 逻辑基于 DOM 元素的 bounding rect。3D canvas 需要正确报告其尺寸给 `useInteractiveRegion`，否则鼠标穿透会失效。
4. **`audio-track.ts` 的 AudioContext**：Electron renderer 里创建 AudioContext 需要用户手势触发（浏览器 autoplay policy）。desktop 的 TTS 播放已经在用户交互后触发，但需确认首次播放时 AudioContext 能 resume。
5. **`companion-store.ts` 的 `$companionLifecycle`**：当前 client/ 原型没有 lifecycle（只有 state），移植时确认 desktop 的 lifecycle（unauthed-egg -> onboarding -> ready）与 3D 渲染的挂载/卸载正确配合。
6. **重命名时机**：Phase 3 的 `git mv desktop client` 必须在 Phase 1-2 完全验证通过后执行——重命名后回退成本高。

---

## 预期最终目录结构（Phase 4 完成后）

```
DeskAgent/
|-- ARCHITECTURE.md          （已更新：不再有 desktop/ 和 client/ 两个模块）
|-- COMPANION_DESIGN.md      （已更新：引用 client/）
|-- AGENTS.md                （已更新：模块表 client/ 替代 desktop/）
|-- README.md                （已更新）
|-- RULES.md                 （已更新：平台策略引用 client/）
|-- plan.md                  （本文件）
|-- backend/                 （不变）
|-- runner/                  （不变）
|-- installer/               （不变，内部引用 desktop -> client）
|-- assets/
|   `-- base-models/         （不变：GLB_MODEL_SPEC.md + generate_base_models.py + README.md）
|-- scripts/                 （已更新：路径 desktop -> client）
`-- client/                  （原 desktop/，整合了 3D 引擎）
    |-- main/                （Electron 主进程：entry.cjs / preload.cjs / ipc/ / runner/ / ...）
    |-- renderer/
    |   |-- companion/
    |   |   |-- 3d/          （新增：Three.js 引擎核心）
    |   |   |   |-- engine.ts
    |   |   |   |-- lighting-rig.ts
    |   |   |   |-- character-controller.ts
    |   |   |   |-- morph-controller.ts
    |   |   |   |-- animation-map.ts
    |   |   |   |-- types.ts
    |   |   |   |-- model-store.ts      （新增：3D 模型 + 换装资产目录）
    |   |   |   `-- companion-3d.tsx    （新增：Three.js Canvas React 组件）
    |   |   |-- boot/
    |   |   |-- onboarding/
    |   |   |-- proactive/
    |   |   |-- sprite/
    |   |   |   |-- sprite-stage.tsx    （保留：窗口/拖拽/click-through）
    |   |   |   |-- egg.tsx             （保留）
    |   |   |   |-- silhouette.tsx      （保留）
    |   |   |   `-- context-menu.tsx    （保留）
    |   |   |-- audio-track.ts          （改动：加 AnalyserNode lip-sync）
    |   |   |-- companion-store.ts      （不动）
    |   |   |-- events.ts               （改动：+ model.ready / wardrobe.updated）
    |   |   |-- tts.ts                  （改动：+ lip-sync 振幅回调）
    |   |   |-- chat-dock.tsx           （不动）
    |   |   |-- root.tsx                （改动：CompanionReady -> Companion3D）
    |   |   `-- ...                     （其余不动）
    |   |-- hub/                        （不动：设置页等 framed 窗口）
    |   `-- shared/                     （不动：UI 组件 / gateway / auth）
    |-- package.json
    |-- tsconfig.json
    `-- vite.config.ts
```
