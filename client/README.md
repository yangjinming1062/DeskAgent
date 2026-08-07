# Client

3D 实时渲染的桌面伙伴客户端。使用 Three.js (WebGL) 替代预渲染视频管线，以"游戏引擎"思维构造角色：素材组合 + 参数驱动动画，而非播放预录内容。

## 设计哲学

当前 desktop 的伙伴渲染依赖预生成视频（portrait → i2v clip），换装需要重生全部视频。Client 用 **3D 实时渲染**替代：后端生成 3D 素材（模型 + 贴图 + morph 参数），客户端用 Three.js 实时合成动画。换装 = 热替服装 mesh，零视频重生。

## 架构（Phase 1 — 引擎原型）

```
engine/
├── Engine.ts              WebGLRenderer(alpha) + Scene + Camera + render loop
├── LightingRig.ts         三点光照 + PMREM 环境贴图（PBR 反射）
├── CharacterController.ts GLB 加载 + AnimationMixer + 程序化兜底角色
├── MorphController.ts     表情/眨眼/口型 morph target 管理（跨格式别名解析）
├── AnimationMap.ts        SpriteState → animation clip 名映射（多别名回退）
└── types.ts
state/
└── companion-store.ts     从 desktop 移植的状态机（优先级门控 + 瞬态语义）
```

引擎是纯 TypeScript + Three.js，无 React 依赖。Phase 3 在引擎之上加 React UI 层。

## 关键设计决策

- **写实风格**：PBR 材质、ACES 色调映射、MeshStandardMaterial。基底模型走 Daz/Reallusion 级别 rigged mesh。
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

- **Phase 2**：backend 3D 素材生成管线（portrait → image-to-3D → rigged GLB）
- **Phase 3**：完整客户端（auth + WS gateway + onboarding + chat + voice call + TTS 口型同步）
- **Phase 4**：换装系统（服装 mesh 生成 + 热替 + wardrobe UI）
- **Phase 5**：移植进 desktop Electron renderer，替换 `companion-ready.tsx` 视频管线
