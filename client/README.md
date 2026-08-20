# Client

桌面伙伴形象载体 + 本地枢纽——单 Electron 应用，承担双职责：底层是可信枢纽（凭证、中转、Runner 编排、自更新），上层是桌面伙伴形象与陪伴交互。两层共享同一个 Electron 主进程，但职责严格分离。

## 1. 职责与边界

**职责**：
- 桌面精灵 3D 实时渲染（Three.js + 透明置顶窗口）+ 陪伴式交互 UI（chat / voice call / onboarding / settings）
- 登录鉴权与用户凭证加密落盘（safeStorage）
- 本地 OS IPC 服务端（命名管道 / UDS）与 Runner 进程生命周期管理
- 双向工具调用路由与反向 RPC 代理中转
- 统一自更新（Electron 二进制 + Runner wheel，两阶段契约）
- **打扰档位的唯一权威**：持有用户偏好 + 活动上下文，独立计算生效值并单向推后端

**不**做：
- **不渲染伙伴人格层**（角色定义 / 长期记忆 / 主动消息生成）——这是后端责任
- **不执行本机工具**——通过 IPC 委托给 Runner
- **不持有 LLM API 凭证**——所有 LLM 调用经后端（即使是反向 RPC 也是经客户端 → 后端 → LLM）
- **不解析协议 schema**（affect / 变形目标等）——后端出枚举，客户端按枚举查找，不擅自扩展

架构层定位见 [ARCHITECTURE.md §1 / §2](../ARCHITECTURE.md)；跨模块契约见 [PROTOCOL.md](../PROTOCOL.md)；产品设计见 [DESIGN.md](../DESIGN.md)。

## 2. 设计意图

- **伙伴层与枢纽层共享主进程，职责严格分离**：底层处理协议与安全（凭证、中转、Runner 编排、自更新——这部分是后端/Runner 复用所依赖的不变契约），上层处理形象渲染与用户体验。伙伴层不直接接触凭证或 Runner 句柄——一切经枢纽层 IPC。
- **3D 实时渲染 + 三级降级**：GLB 加载成功时骨骼动画 + 面部变形目标覆盖全部状态；GLB 不可用（生成中/失败/无 key/换模空挡）时静态精灵相册接管——按状态/情绪向后端相册请求身份一致的透明背景立绘，淡入淡出切换，GLB 解析完成后交还；相册不可用才渲染程序化兜底蛋（呼吸/眨眼/说话浮动），保证形象从启动第一帧起就"活着"且永远是用户选定的角色。
- **多骨骼动画库 + 性格标签驱动**：7 大骨骼体系（人形最全，100+ 动作），按模型骨骼类型注入对应动画库，按伙伴性格标签交集匹配驱动动作调度。详见 [docs/MODEL_SPEC.md §2](../docs/MODEL_SPEC.md)。
- **打扰档位唯一权威**：客户端持有用户偏好（本地持久化）+ 活动上下文（活动感知器），独立计算生效值（应用「手动安静永远不被覆盖」+ 沉浸式/全屏 → 安静规则），单向推后端；后端持有的只是镜像，不是独立推导。契约见 [ARCHITECTURE.md §5.1](../ARCHITECTURE.md)。
- **透明置顶精灵窗口作为唯一常驻主窗口**：登录、应用设置、登录态界面是从托盘唤起的按需工具窗口，不常驻——"对话发生在角色身边"。Windows close = 隐藏到托盘；macOS close = 隐藏窗口但保留 Dock 图标。
- **网关连续性与去重重放**：客户端记录连接级单调序列号并对网络重叠帧幂等去重，定期批量向服务端确认消费进度；断线重连携带水位触发增量重放，网络抖动下流式对话和工具调用无感续接；服务端重启或序列号失同步时自动重置水位防新事件黑洞（普通活连接会话切换不重置）。契约见 [PROTOCOL.md §0](../PROTOCOL.md)。

## 3. 架构地图

```
client/
├── main/                  # TypeScript *.ts — Electron 主进程（经 tsup 编译打包至 dist-electron/）
│   ├── entry.ts           # 启动入口 + 自更新挂载
│   ├── preload.ts         # Preload 桥接脚本
│   ├── ipc/               # IPC handler 命名空间（auth / media / connection / runner 等）
│   ├── runner/            # Runner 进程编排（bridge + reverse-rpc + updater）
│   ├── security/          # 路径白名单 + 凭证保护 + hardening
│   ├── lifecycle/         # tray + 单实例锁 + 关闭拦截
│   ├── backend/           # REST 客户端 + session 会话管理 + ws 探测
│   └── shared/            # 强类型 IPC 契约定义与通用工具库
├── renderer/              # ESM *.{ts,tsx} — Vite 编译
│   ├── shared/            # 跨窗口共享层
│   ├── companion/         # 伙伴层（精灵窗口 + 3D + onboarding + chat UI）
│   │   └── 3d/            # Three.js 引擎 + 动画 clip 库
│   ├── hub/               # 枢纽层（托盘唤起的工具窗口）
│   ├── clip-debugger/     # 独立动画调试套件（pnpm clip 启动，跳过 LLM 链路直连 3D 动作检视）
│   ├── app.tsx            # 角色分发点
│   └── main.tsx
├── scripts/               # 构建/测试钩子
└── assets/                # icon
```

**TypeScript 全栈类型安全**：主进程采用 `main/*.ts`（经 `tsup` 统一编译输出 CJS 产物至 `dist-electron/`），渲染进程采用 `renderer/**/*.{ts,tsx}`（Vite 编译）。通过主进程的 IPC 契约定义与渲染进程的全局类型声明共享严格的 IPC 通道与载荷契约。

**renderer 内部跨模块边界**：`companion` ↔ `hub` 是**两个窗口**而非一个工程的两个层——它们的代码历史上不该相互依赖：

| 起点 → 终点 | 许可 |
|---|---|
| `companion` → `shared` | ✓ |
| `hub` → `shared` | ✓ |
| `companion` ↔ `hub` | ✗（任何 `from '@/hub/...'` 出现在 companion 文件都会被 ESLint 拒绝） |
| `shared` → 任何 | ✗ |

ESLint `no-restricted-imports` 在 `renderer/companion/**` 与 `renderer/hub/**` 各设一道拦截。**唯一例外**是 `renderer/app.tsx`——这是角色分发点，需要同时 import 两个 root。

模块公共面经 `index.ts` barrel 暴露（`@/companion`、`@/hub`、`@/shared`、`@/shared/components/ui`）。模块内部细节不出 barrel。

## 4. 关键设计决策

- **精灵窗口透明需要双重保证**：窗口级透明标志**加**渲染层 body 透明（由内嵌脚本在 head 解析时同步设置角色属性）。两者缺一，body 背景色会在桌面剩余区域盖满屏幕——违背"伙伴应不干扰用户正常工作"的契约。
- **交互范围仅限可见矩形（透明窗口交互陷阱）**：Electron 的鼠标穿透是窗口级二元开关——要么全捕获要么全穿透。要在屏幕尺寸的透明窗口里只让"看得见"的区域捕获，所有弹层把面板矩形注册到统一的命中登记处，由舞台的全局移动事件唯一做命中测试再切换穿透。任何弹层都不能自行一刀切捕获整个窗口——那会立刻把桌面的其他应用"锁死"。
- **精灵窗口弹层不走主题变量**：主题变量在精灵窗口按浅色解析（白底容器），而弹层正文全是硬编码白字——主题变量配硬编码白字会白上白不可读。因此对话/伙伴设置/长期记忆/语音通话/对话列表面板统一硬编码深色半透明容器；新增弹层必须跟这一族，主题变量只属于 hub 工具窗口。同族交互契约：面板头部统一接同一拖拽钩子（位移本地持久化、命中区域自动跟随）、打开入口统一经互斥收口——同一时刻最多一个面板在屏，避免弹层堆叠。
- **工具窗口（`?role=tool`）钉死深色显式调色板**：该窗口把全部语义 token 固定为与精灵弹层同族的深色显式值，无皮肤/模式切换，一处硬编码；主题 boot 对此窗口跳过（内联主题会盖过钉死值），精灵窗口的浅色主题不得改写工具窗口 overlay。主进程背景色与标题栏用同一深色且不跟随 OS 外观。设置页经系统原生窗口控件覆盖层全出血渲染（唯一形态：拖拽带 + Esc 关闭；Win/Linux 关闭由原生控件承担，应用侧关闭钮只在 macOS 渲染）。**标题栏高度常量必须与样式变量一致**——侧栏/主区内边距与拖拽带高度都由它推导，两边漂移会让内容钻进原生按钮下。
- **激活码持久 + 会话 JWT 仅内存**：磁盘只持久化加密激活码 + 服务地址 + 用户；每次启动用激活码换取新的会话 JWT（含主动刷新机制）。为什么不持久会话 JWT：一旦持久就要承担泄露 + 过期管理成本；激活码 + 每次启动重新激活的模式更安全。
- **自更新两阶段而非单阶段**：单阶段"下载后直接覆盖"在网络断/进程被杀时变砖；两阶段拆分让第一阶段（旧进程跑）只做下载 + 强校验，第二阶段（新进程跑）才做文件操作，失败回滚旧版。为什么不直接原子重命名：原子重命名之前同样需要先完整下载到 staging，与两阶段本质等价，但分阶段语义上更易追踪哨兵标记与降级。契约见 [PROTOCOL.md §5.5](../PROTOCOL.md)。
- **STT 默认本地优先 / TTS 默认云端优先**（见 [DESIGN.md §7](../DESIGN.md)）：本地零成本，云端音色音质优；引擎路由在 IPC 边界读短 TTL 缓存决策（自动 / 本地 / 云端三档），不暴露在设置面板——运维/部署侧决策。
- **音色 id 不跨引擎**：云端音色 id 与本地音色 id 属于不同命名空间；路由到本地时不传调用方的音色。用户在伙伴设置中选的音色仅在云端路径生效。
- **3D 渲染栈 WebGPU + 四层回退**：引擎异步工厂按 WebGPU → 内置 WebGL2 节点后端（同一 API 面/场景图，零代码）→ 经典 WebGLRenderer → 初始化失败（静态精灵层兜底，永不空白）逐级降级。**经典回退必须换新 canvas**——曾成功获取过 WebGPU 上下文的 canvas 再也要不到 WebGL2 上下文，所以 canvas 由引擎自建自管（React 只渲染容器），模型加载一律等待引擎就绪而非早退，避免首模型在异步启动窗口被静默丢弃。环境贴图生成按渲染器类型分支——经典版深度依赖 WebGLRenderer 内部结构。为什么不停留在经典 WebGL：混合显卡笔记本上高性能偏好会强制唤醒独显（桌面伙伴负载远低于该门槛）。
- **3D 材质安全回退与拖拽动力学**：加载 GLB 时记录模型内嵌原生基础贴图，自定义 PBR 贴图 404 或网络故障时自动回退原生材质，杜绝模型白板。拖拽时捕获即时速度向量注入物理惯性倾角（横滚/俯仰），结合"悬空摆动"与"落地缓冲"专属动作呈现被"拎起"的交互质感；精灵基准尺寸随屏幕高度自适应，兼容高分辨率屏。
- **精灵窗口单显示器跟踪 + 跨屏拖拽接力**：透明精灵窗口同一时刻只覆盖一块显示器（贴合当前显示器工作区；分辨率变化原地重贴，显示器被拔掉时自动落回最近屏）。拖拽中指针越出视口即光标已跨入邻屏——渲染层请主进程把窗口挪到光标所在屏；渲染层只把精灵位置平移新旧窗口原点差、拖拽基准不动（切换后指针坐标已在新视口空间，拖拽公式自然产出平移后的值），并按返回的光标点判定最新指针坐标属于旧/新视口空间，避免接力往返期间已到达的新空间事件被二次平移；拖拽结束时窗口原点随位置一并持久化，下次启动先贴回精灵所在显示器再恢复位置。为什么不做覆盖整个虚拟桌面的窗口：全桌面合成层 + 跨屏 DPI 差异的坐标/命中测试复杂度远高于单屏窗口接力，且鼠标穿透范围会被迫覆盖所有屏。
- **渲染循环自研调度与能耗档位**：引擎自主调度动画循环（不依赖 Three.js 内部循环），支持活跃全速 / 空闲降频 / 休眠低频轮询与彻底停止，解决休眠档位能耗控制。
- **3D 模型传输与本地缓存**：3D 模型 GLB 采用 Draco 压缩，渲染端流式解压（解码器本地托管）；主进程按内容哈希磁盘缓存，支持断点续传与 LRU 淘汰。传输契约见 [PROTOCOL.md §1.5](../PROTOCOL.md)。
- **模型下载失败与生成失败分流**：失败事件载荷带"可重试下载"标记时，生成结果已付费且仍留在后端——失败浮层只给"重试下载"（绝不重新计费），不给"重新生成"入口；启动水合收到下载失败行同样进该态，避免每次启动静默重刷一次计费生成。契约见 [PROTOCOL.md §1.2](../PROTOCOL.md)。
- **独立 3D 动画与模型调试套件（`pnpm clip`）**：为解决 3D 动作、面部变形目标与后端 GLB 模型质量验证严重依赖完整 LLM 对话链路、反馈慢的问题，提供全屏热更的独立调试器：激活码自动鉴权一键从后端下载模型并流式 Gzip 解压；7 大骨骼体系全动作库即点即播、交叉淡入淡出与逐帧步进；包围盒接地、水平居中与 Z-up 平躺模型自动立起；位移/旋转/缩放交互手柄；面部变形目标实时调校与 TTS 嘴型振幅模拟。
- **Windows 单实例锁 dev 退出**：设 `SPIRITAGENT_DESKTOP_DISABLE_SINGLE_INSTANCE_LOCK=1` 强制多实例运行，便于并行调试窗口。
- **连发消息合并窗口**：用户快速连发多条时，聊天层用数秒防抖窗口把消息合并成一次批量提交，只触发一次 LLM 调用（[DESIGN.md §6.6](../DESIGN.md)）。这是**刻意**的合并，不是发送延迟：窗口内逐条重置计时器，且回合完成 / 错误 / 用户停止时会立即冲刷。

## 5. 与外部的契约

| 契约 | 方向 | 在哪定义 |
|------|------|---------|
| 伙伴层 JSON-RPC 方法（onboarding / avatar / companion / model / tts） | 对后端 | [PROTOCOL.md §1.2](../PROTOCOL.md) |
| 事件类型（`companion.affect` / `companion.assets.updated` / `model.ready` 等）与聊天流事件（`message.start` / `message.delta` / `message.break` / `message.complete` / `tool.*` / `error`） | 接后端推送 | [PROTOCOL.md §1.3](../PROTOCOL.md) |
| Affect emotion / locale 枚举消费 | 接后端 | [PROTOCOL.md §1.4](../PROTOCOL.md) |
| 资产 URL 5 分钟 HMAC 签名消费 + 本地缓存 | 接后端 | [PROTOCOL.md §1.5](../PROTOCOL.md) |
| 错误信封 `{error, reason, status}` + JSON-RPC 错误码脱敏消费 | 接后端 | [PROTOCOL.md §1.6](../PROTOCOL.md) |
| Runner `runner_ready` payload + capabilities 消费 | 对 Runner | [PROTOCOL.md §2.3](../PROTOCOL.md) |
| 反向 RPC `request_llm` 代理 | 对 Runner + 后端 | [PROTOCOL.md §3](../PROTOCOL.md) |
| 反向 RPC 速率守卫（200 帧；文本 1MB / 多模态视觉 10MB 上限） | 对 Runner（客户端转发前限流） | [PROTOCOL.md §3](../PROTOCOL.md) |
| 打扰档位权威边界 | 对后端（客户端推、后端镜像） | [ARCHITECTURE.md §5](../ARCHITECTURE.md) + [DESIGN.md §6.2](../DESIGN.md) |
| 打扰档位双层模型（用户偏好 + 生效覆盖 + 生效值） | 本模块独有（持久层 + 活动感知器） | 本 README §2 + DESIGN §6.2 |
| LLM 反应与自主开关（本地持久化，不上报后端） | 本模块独有 | [DESIGN.md §6.3](../DESIGN.md) |
| safeStorage 跨平台一致（DPAPI/Keychain/libsecret） | 平台 | [PROTOCOL.md §5.3](../PROTOCOL.md) |
| Electron 二进制自更新（`electron-updater` RSA + Runner wheel RSA + SHA-512） | 对后端 | [PROTOCOL.md §5.5](../PROTOCOL.md) |
| 自更新两阶段契约（Stage 1 prefetch / Stage 2 install + Sentinel + 降级） | 对后端 | [PROTOCOL.md §5.5](../PROTOCOL.md) |
| IPC 命名空间 `spiritagent:*` 前缀（`spiritagent:sprite:*` / `spiritagent:auth:changed` 等） | 本模块独有 | 本 README §3 |
| 动画状态机（`IDLE` / `LISTENING` / `THINKING` / `SPEAKING` / `WORKING` / `EMOTIONAL` / `SLEEPING` / `INTERACTING` / `DISCONNECTED`） | 本模块独有（消费后端 `affect` + 用户操作） | 本 README §2 + DESIGN §2 |
| 空间行为场所：协议 5 项（`home` / `chat` / `perch` / `roam` / `sleep`）+ 客户端内部 `target`（仪式行走专用，工具调用触发、非协议枚举）+ 缩放范围 0.5×–2× | 本模块独有（消费后端 `affect.locale`；`target` 仅本模块内部触发） | 本 README §2 + DESIGN §3 + PROTOCOL §1.3 |
| 伙伴性格标签驱动的动画调度 | 本模块独有 | 本 README §2 + [docs/MODEL_SPEC.md §2](../docs/MODEL_SPEC.md) |
| 激活码格式（base64 编码 `{b, t}` JSON） | 对后端 | [PROTOCOL.md §5.3](../PROTOCOL.md) |
| Skills frontmatter 平台过滤（仅 `macos` / `windows`，历史 `linux` 值兼容翻译表） | 本模块独有 | 本 README §3 + [installer/README.md §2](../installer/README.md) |

## 6. 已知限制

| 限制 | 说明 |
|------|------|
| **WebGPU 透明合成依赖 premultiplied** | 透明画布按预乘 alpha 配置；透明精灵窗下若出现黑晕/黑底即走回退链（决策写 dev log） |
| **Electron 42 + pnpm 11 需 hoisted** | 失去 phantom-deps 防护；等 Electron ESM 主进程支持 |
