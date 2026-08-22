# Runner

本地手脚——纯粹的工具执行器，承载伙伴"能帮用户做的事"。Runner 以 wheel 形式发布，由安装器落盘、客户端启动，经本地 IPC 承载的 JSON-RPC 2.0 调用在用户机器上执行工具。

Runner 不感知"伙伴"语义——终端、文件、浏览器、代码执行等底层能力 100% 保留，伙伴人格完全由后端承载、伙伴形象完全由客户端渲染。

## 1. 职责与边界

**职责**：在用户本机执行后端 LLM 决策调用的工具（终端 / 文件 / 浏览器 / 代码沙箱 / 系统感知 / 音频 / MCP 动态加载 / Skills）；就绪握手时上报本地 OS 能力（真实探测，见 §2）；按需经反向 RPC 借客户端 → 后端调用 LLM。

**不**做：
- **不持有后端 Token 或云端地址**——需借 LLM 时通过反向 RPC 由客户端代理。
- **不装配对话提示词 / 不管理记忆 / 不调度对话**——这些是后端责任。
- **不持久化伙伴状态**——所有状态都是进程内的。

架构层定位见 [ARCHITECTURE.md §1 / §2](../ARCHITECTURE.md)；客户端 ↔ Runner 协议见 [PROTOCOL.md §2](../PROTOCOL.md)；反向 RPC 桥接见 [PROTOCOL.md §3](../PROTOCOL.md)；安全防线见 [ARCHITECTURE.md §7](../ARCHITECTURE.md)。

## 2. 设计意图

- **剥离大脑逻辑**：系统提示词、供应商适配、对话记忆全部由后端承载。Runner 是单纯的"接 JSON-RPC 工具调用 → 执行 → 返回结果"的执行器。
- **零凭证 / 无网络出站**：Runner 不保存任何用户 Token 或云端地址，无法直接访问后端。所有需 LLM 的工具走反向 RPC 经客户端代为调用（[PROTOCOL.md §3](../PROTOCOL.md)）。**这是不可破坏的不变量**——即便 prompt 注入攻陷 Runner 工具逻辑，最坏情况也只是借客户端调用受限用户账户下的 LLM，不会泄露后端凭证。
- **Responses 输入边界**：Runner 工具可提交原生 Responses 指令与输入项，也可提交旧消息数组；Client 在零凭证代理边界统一成后端契约，供应商选择与兼容性过滤由后端承载。
- **环境状态与工具解耦**：`envs/` 作为顶层包承载环境执行与共享态（Local / Docker / Singularity / SSH，活跃实例表、工厂、生命周期清理），工具层（`terminal` / `execute_code` / `files` / `process`）统一依赖 `envs`；`envs` 不依赖 `tools`，通过回调钩子（`register_env_cleanup_hook` / `register_active_process_checker`）实现生命周期与缓存解耦。
- **能力上报尽量运行时探测**：麦克风（枚举 WASAPI/AVFoundation 设备）、屏幕捕获（枚举监视器）、系统活跃度（真实调底层 API）是运行时探测；本地 STT/TTS 是执行原生加载器的 import 探测（其 import 会加载推理二进制，失败即不可用）。不用存在性检查——那会欺骗 UI 让用户点不能用按钮。
- **音频引擎默认在基础 wheel 内**：本地语音栈核心依赖（faster-whisper / piper-tts / sounddevice / numpy）从基础 wheel 直接可用（[DESIGN §7](../DESIGN.md)）；`pyttsx3` 用平台 marker 限制（macOS / Windows 上有 SAPI5 / NSSpeechSynthesizer 兜底）。运行时仍要求系统 PATH 有 `ffmpeg`。
- **Skills 安全扫描的信任边界**：THREAT 扫描与结构检查对 community 来源强制执行；skill 自带的 ignore 文件只对 builtin/trusted 来源生效——不可信 skill 不能用自己的 ignore 文件关闭对自己的安全门禁。

## 3. 架构地图

```
runner/
├── server.py              # 唯一 IPC 入口；分发所有 RPC 方法
├── envs/                  # 环境执行与生命周期（Local / Docker / Singularity / SSH / FileSync）
├── tools/                 # 工具子包，按 domain 拆分
│   ├── terminal/          # 终端交互工具
│   ├── files/             # 文件读写 + 跨域写保护
│   ├── browser/           # 浏览器多后端（CDP / Camofox）
│   ├── execute_code/      # 代码沙箱
│   ├── process/           # 进程管理与 PTY 交互
│   ├── skills/            # Skills 加载与过滤
│   ├── mcp/               # 动态 MCP 客户端（含 mcp_supervisor）
│   ├── multimodal/        # 视觉理解 / 图像生成
│   ├── system/            # 系统感知（焦点 / 空闲 / 屏幕 / 麦克风）
│   └── toolsets/          # 工具集启用/禁用配置
└── utils/                 # 纯 helper 层：路径 / 配置 / 脱敏 / 文件安全 / PID / 反向 RPC / capabilities / Desktop IPC 传输 / URL 安全
```

依赖方向：`utils/` ← `envs/` ← `tools/` ← `server.py`，底层模块绝不反向依赖上层模块；工具间公共环境能力统一自 `envs/` 导入。

**Wheel 产物**：`dist/spirit-agent-*.whl`。客户端按安装器建立的运行时布局启动 Runner；布局见 [installer/README.md](../installer/README.md)。

## 4. 关键设计决策

- **本地 IPC 客户端而非 stdio**：Runner 主动连接客户端下发的本地端点（Windows Named Pipe 重叠 I/O 或 macOS UDS），借用 `websockets` sans-I/O 协议解析器实现轻量安全帧处理，规避 TCP loopback 端口监听暴露面；重连时重读端点信息以跟随客户端重启；链路选型与鉴权见 [ARCHITECTURE.md §4.1B](../ARCHITECTURE.md) 和 [PROTOCOL.md §2.1](../PROTOCOL.md)。
- **统一 HTTP 客户端栈**：移除 `requests` 与 `aiohttp` 重复依赖，所有同步/异步 HTTP 请求统一收敛到 `httpx[socks]`，减小 wheel 体积与审计面。
- **SSRF 建连前 + 建连时双重校验**：`SafeHTTPTransport` / `SafeAsyncHTTPTransport` 在 `handle_request` 之前对 URL 字符串做白名单与（hostname + IP 字面量）预检；最终 socket.connect 不再走 httpcore 默认的 `socket.create_connection`，而是被替换为 `_SafeSyncBackend` / `_SafeAsyncBackend`，在每次建连时强制重新调用 `getaddrinfo` 校验所有解析结果，并直接使用已校验 IP 建连，原始 Host / TLS SNI / 证书主机名校验保持不变。重定向后每一跳都重新走同一校验路径，DNS 解析被移到工作线程避免阻塞事件循环，彻底消灭预检到建连之间的 TOCTOU 窗口。
- **反向 RPC 由客户端守门**：Runner 只发起请求，不在本地维护云端凭证或自行限流；`call_llm_sync` 在工作线程安全等待主循环 Future，超时自动取消；契约见 [PROTOCOL.md §3](../PROTOCOL.md)。
- **MCP 进程监管与熔断保护**：`mcp_supervisor.py` 统一管理所有 stdio 子进程生命周期，自动回收孤儿进程并对故障服务器实施带冷却的熔断（Circuit Breaker），防止向失效端点风暴重试。
- **Windows Job Object 内核级进程树生命周期绑定**：Runner 启动阶段显式将自身加入"关闭即杀全树"的 Job Object，派生的所有子进程/孙进程/PTY 终端自动继承；模块导入无隐式副作用，Runner 异常崩溃或被杀时由 Windows 内核原子强杀全进程树，杜绝孤儿进程悬挂。
- **Win32 原生路径规范化**：经 `GetFinalPathNameByHandleW` 回溯解析真实路径，覆盖 8.3 短文件名、符号链接、目录联接点与深层未创建子路径；统一大小写不敏感比对、剥离 NT/UNC 设备前缀并拦截 NTFS 备用数据流。
- **依赖显式声明而非 try-except import**：Runner 以 uv wheel 分发、装到用户机器后依赖集即冻结、无法中途增补——所有 pip 依赖一律显式声明（含平台 marker），"有就是有、没有就是没有"，不需要在导入时再判断。try-except import 只允许两类合法场景：① 运行时能力探测（故意执行原生加载器的 import 验证二进制真能加载）；② OS 框架/平台导入（ctypes / Quartz / pythoncom 等非 pip 依赖）。
- **代码执行沙箱 RPC 令牌鉴权**：每次代码执行生成一次性能力 token，子进程首帧校验；Windows loopback TCP 端点防范本地未授权进程访问。
- **单进程 1:1 架构模型**：多用户或多实例场景下每个客户端单独 spawn 专属 Runner 进程，天然隔离各用户的本地权限、环境变量与进程上下文。

## 5. 与外部的契约

| 契约 | 方向 | 在哪定义 |
|------|------|---------|
| 就绪握手、能力上报与 RPC 方法 | 对客户端 | [PROTOCOL.md §2](../PROTOCOL.md) |
| 反向 RPC 与速率守卫 | 经客户端到后端 | [PROTOCOL.md §3](../PROTOCOL.md) |
| 本地执行安全防线 | 对本地工具执行 | [ARCHITECTURE.md §7](../ARCHITECTURE.md) |
| MCP 动态加载与 Skills 平台过滤 | 本模块独有 | 本 README §2 / §4 |
| 音频运行时依赖打包 | 与 Installer 协作 | [installer/README.md](../installer/README.md) |

## 6. 已知限制

| 限制 | 说明 |
|------|------|
| **TTY/stdin 不可用** | 所有 RPC 经本地 IPC 链路；任何 stdin 重定向或直接 console 输入都会与 C 库底层日志污染协议帧 |
