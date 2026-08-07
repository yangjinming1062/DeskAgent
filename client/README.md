# Client

3D 实时渲染的桌面伙伴客户端。**本目录是开发原型，最终渲染引擎将移植到 desktop/renderer/companion/3d/**（Phase 5）。使用 Three.js (WebGL) 替代预渲染视频管线，以"游戏引擎"思维构造角色：素材组合 + 参数驱动动画，而非播放预录内容。

## 设计哲学

当前 desktop 的伙伴渲染依赖预生成视频（portrait → i2v clip），换装需要重生全部视频。Client 用 **3D 实时渲染**替代：后端按物种选择预制 rigged GLB 基底模型即时下发，后台异步生成个性化纹理；客户端用 Three.js 实时合成骨骼动画 + morph 表情。换装 = 热替材质/纹理，零模型重生。

## 架构

`
engine/
├─ Engine.ts              WebGLRenderer(alpha) + Scene + Camera + render loop
├─ LightingRig.ts         三点光照 + PMREM 环境贴图（PBR 反射）
├─ CharacterController.ts GLB 加载 + AnimationMixer + 换装热替 + 程序化兜底
├─ MorphController.ts     表情/眺眼/口型 morph target 管理（跨格式别名解析）
├─ AnimationMap.ts        SpriteState → animation clip 名映射（多别名回退）
└─ types.ts
gateway/
├─ GatewayClient.ts       WS JSON-RPC 2.0 客户端（请求/响应匹配 + 事件分发）
└─ types.ts
state/
├─ companion-store.ts     状态机（优先级门控 + 瞬态语义）
├─ auth-store.ts          JWT 登录 + ws-ticket
├─ chat-store.ts          对话消息（流式合并）
├─ gateway-store.ts       网关连接状态
├─ wardrobe-store.ts      换装目录 + 当前装备项
└─ events.ts              Backend 事件 → 状态机 + TTS 分发
ui/
├─ App.tsx               Auth 生命周期 + gateway 连接 + 事件接线
├─ CompanionCanvas.tsx   3D 引擎 React 封装（状态订阅 + lip sync）
├─ ChatDock.tsx          对话 UI（消息列表 + 发送）
├─ WardrobePanel.tsx     换装 UI（浏览预设 + 装备）
└─ LoginScreen.tsx       登录表单
tts.ts                       TTS 播放 + Web Audio API 振幅分析 → morph 嘴型同步
api.ts                       REST 客户端（auth/persona/model/TTS/wardrobe）

## 关键设计决策

- **写实风格**：PBR 材质、ACES 色调映射、MeshStandardMaterial。后端按物种提供预制 rigged GLB 基底模型（人类/精灵/灵兽/机甲/幻形 + 通用兜底），完全写实风格。
- **透明背景**：`alpha: true` + `setClearColor(0, 0)`，为 Electron 透明置顶窗口而设。Phase 1 在浏览器中验证，Phase 5 移植进 Electron。
- **GLB 优先 + 程序化兜底**：CharacterController 先尝试加载 GLB（带骨骼动画 + morph targets），失败则退化为程序化角色（Three.js 基本体组合 + 正弦动画）。引擎始终可运行，不依赖外部模型。
- **Morph 别名系统**：不同模型格式（ARKit / VRM / Daz / 自定义）的 morph target 名不一。MorphController 维护语义名 → 别名列表映射，加载时解析出实际存在的 morph 索引。
- **状态机不变量**：从 desktop `companion-store.ts` 移植，保持优先级门控（`disconnected > interacting > working > speaking > thinking > listening > emotional > sleeping > idle`）和瞬态语义（emotional / interacting 自动恢复）。

## 运行

```bash
cd client
pnpm install
pnpm dev          # → http://127.0.0.1:5175
```

- **左键单击**：循环状态（idle → listening → thinking → speaking → working → sleeping → disconnected）
- **右键**：循环情绪表情（happy → sad → surprised → …）
- **空格**：同左键
- **拖拽**：水平移动角色
- **鼠标移动**：角色头部跟随鼠标（look-at）
- **加载 GLB**：`?model=./path/to/character.glb`（需含骨骼动画 + morph targets）

## 后续阶段

- **Phase 2**：backend 3D 素材生成管线（多物种 GLB 基底 + base_texture/meshy 双 provider + AI 纹理）——已完成
- **Phase 3**：完整客户端（auth + WS gateway + chat + TTS 口型同步）——已完成
- **Phase 4**：换装系统（材质预设 + AI 纹理生成 + 热替 + wardrobe UI）——已完成
- **Phase 5**：将引擎核心（engine/ + state/ + tts.ts）移植到 desktop/renderer/companion/3d/，替换 `companion-ready.tsx` 视频管线。UI 层（auth/chat/wardrobe）与 desktop 现有的 hub 窗口结构融合，非精灵部分（设置页等）保留复用。模型规格见 [GLB_MODEL_SPEC.md](../assets/base-models/GLB_MODEL_SPEC.md)。
