# Runner

本地手脚——纯粹的工具执行器，承载伙伴"能帮用户做的事"。以 uv build wheel 形式发布，安装器在 `$SPIRITAGENT_HOME/runner/.venv` 创建 venv 并安装；客户端直接 spawn venv Python 调用 `server.py`，通过本地 OS IPC（Windows 命名管道 / macOS UDS，承载 WebSocket 帧）接收 JSON-RPC 2.0 工具调用指令并在用户机器上执行。

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
- **供应商范围**：产品 LLM 交互只面向 OpenAI-compatible 供应商，不接 Anthropic。nullable union 原样传递，由目标供应商决定能否接受。
- **环境状态与工具解耦**：终端环境共享态（活跃实例表、工厂、清理线程）下沉到终端工具的 environment 子包，文件与代码执行工具跨包共享同一批环境实例，绕开仍含命令处理 + 安全审批逻辑的终端主模块，避免循环依赖。
- **能力上报尽量运行时探测**：麦克风（枚举 WASAPI/AVFoundation 设备）、屏幕捕获（枚举监视器）、系统活跃度（真实调底层 API）是运行时探测；本地 STT/TTS 是执行原生加载器的 import 探测（其 import 会加载推理二进制，失败即不可用）。不用存在性检查——那会欺骗 UI 让用户点不能用按钮。
- **音频引擎默认在基础 wheel 内**：本地语音栈核心依赖（faster-whisper / piper-tts / sounddevice / numpy）从基础 wheel 直接可用（[DESIGN §7](../DESIGN.md)）；`pyttsx3` 用平台 marker 限制（macOS / Windows 上有 SAPI5 / NSSpeechSynthesizer 兜底）。运行时仍要求系统 PATH 有 `ffmpeg`。
- **Skills 安全扫描的信任边界**：THREAT 扫描与结构检查对 community 来源强制执行；skill 自带的 ignore 文件只对 builtin/trusted 来源生效——不可信 skill 不能用自己的 ignore 文件关闭对自己的安全门禁。

## 3. 架构地图

```
runner/
├── server.py              # 唯一 IPC 入口；分发所有 RPC 方法
├── tools/                 # 工具子包，按 domain 拆分
│   ├── terminal/          # 终端执行（含 environment/ 子包承载共享态）
│   ├── files/             # 文件读写 + 跨域写保护
│   ├── browser/           # 浏览器多后端（Playwright / Camofox）
│   ├── execute_code/      # 代码沙箱
│   ├── process/           # 进程管理
│   ├── skills/            # Skills 加载与过滤
│   ├── mcp/               # 动态 MCP 客户端
│   ├── multimodal/        # 视觉理解 / 图像生成
│   ├── system/            # 系统感知（焦点 / 空闲 / 屏幕 / 麦克风）
│   ├── toolsets/          # 工具集启用/禁用配置
│   └── security/          # Tirith 扫描 / 路径白名单
└── utils/                 # 纯 helper 层：路径 / 配置 / 脱敏 / 文件安全 / PID / 反向 RPC / capabilities / Desktop IPC 传输
```

依赖方向：`utils/` ← `tools/` ← `server.py`，`utils/` 不反向依赖任何工具；`tools/` 之间跨包直接 import 共享子包（如 `terminal/environment/`）。

**Wheel 产物**：`dist/spirit-agent-*.whl`。客户端 spawn `$SPIRITAGENT_HOME/runner/.venv/{bin/python,Scripts/python.exe} $SPIRITAGENT_HOME/runner/server.py --desktop-endpoint <path> --desktop-auth <token>`。安装布局详 [installer/README.md](../installer/README.md)。

## 4. 关键设计决策

- **OS IPC + WebSocket 帧而非 stdio 重定向**：客户端监听命名管道（Windows）/ UDS（macOS），Runner 主动连入，链路承载 WebSocket 帧协议——避免 C 库底层日志或 Python print 污染标准输入输出帧；全双工并发按 id 异步匹配响应；零端口暴露，端点与握手 token 由客户端单向下发、Runner 重连间重读文件跟随客户端重启（完整权衡见 [ARCHITECTURE.md §4.1B](../ARCHITECTURE.md)，鉴权细节见 [PROTOCOL.md §2.1](../PROTOCOL.md)）。为什么不用 stdio：PTY/C 库会与控制台帧冲突，异步 RPC 也会被全局锁退化为串行。
- **反向 RPC 速率守卫在客户端而非后端**：转发前统计单会话请求次数与载荷大小硬限流（契约见 [PROTOCOL.md §3](../PROTOCOL.md)）。为什么是客户端：客户端是流量入口，能在 IPC 边界做最严格的拒绝。
- **Windows Job Object 内核级进程树生命周期绑定**：Runner 启动阶段显式将自身加入"关闭即杀全树"的 Job Object，派生的所有子进程/孙进程/PTY 终端自动继承；模块导入无隐式副作用，Runner 异常崩溃或被杀时由 Windows 内核原子强杀全进程树，杜绝孤儿进程悬挂。
- **Win32 原生路径规范化**：经 `GetFinalPathNameByHandleW` 回溯解析真实路径，覆盖 8.3 短文件名、符号链接、目录联接点与深层未创建子路径；统一大小写不敏感比对、剥离 NT/UNC 设备前缀并拦截 NTFS 备用数据流。
- **Tirith 扫描作为 shell 命令前置过滤器**：所有 shell 命令执行前拉起本地 Tirith 模块做参数签名与静态审查，默认 fail-secure，二进制未安装时友好降级并告警。为什么不在 LLM 层做：LLM 层做参数校验是治标，执行边界做拦截更彻底。
- **SSRF 建连前 + 建连时双重校验**：仅域名解析预检会被预检-建连之间的 DNS 重绑定绕过；httpx 的 connect 事件钩子捕获实际目标 IP 二次过滤。为什么不只信任 URL 形式：URL 里的 host 解析后可以被重新指向。
- **依赖显式声明而非 try-except import**：Runner 以 uv wheel 分发、装到用户机器后依赖集即冻结、无法中途增补——所有 pip 依赖一律显式声明（含平台 marker），"有就是有、没有就是没有"，不需要在导入时再判断。try-except import 只允许两类合法场景：① 运行时能力探测（故意执行原生加载器的 import 验证二进制真能加载）；② OS 框架/平台导入（ctypes / Quartz / pythoncom 等非 pip 依赖）。对已声明依赖残留的 try-except 属历史遗留，方向是移除。
- **代码执行沙箱 RPC 令牌鉴权**：每次代码执行生成一次性能力 token，子进程首帧校验；Windows loopback TCP 端点防范本地未授权进程访问。
- **单进程 1:1 架构模型**：多用户或多实例场景下每个客户端单独 spawn 专属 Runner 进程，天然隔离各用户的本地权限、环境变量与进程上下文。

## 5. 与外部的契约

| 契约 | 方向 | 在哪定义 |
|------|------|---------|
| `runner_ready` payload（含 version + capabilities + capabilities_health） | 对客户端 | [PROTOCOL.md §2.3](../PROTOCOL.md) |
| `spiritagent.info` 完整运行快照 | 对客户端 | [PROTOCOL.md §2.2](../PROTOCOL.md) |
| RPC 方法清单（`runner_ready` / `get_tools` / `execute_tool` / `spiritagent.cancel` / `spiritagent.config.update` / `spiritagent.info` / `mcp.reload` / `request_llm` / `tools_changed`） | 对客户端 | [PROTOCOL.md §2.2](../PROTOCOL.md) |
| 反向 RPC 桥接（`request_llm` → 客户端 → `/api/llm/completion`） | 对客户端 | [PROTOCOL.md §3](../PROTOCOL.md) |
| 反向 RPC 速率守卫（200 帧；文本 1MB / 视觉 10MB 上限） | 对客户端（客户端转发前限流） | [PROTOCOL.md §3](../PROTOCOL.md) |
| Reserved Keys 不适用 | — | Reserved Key 是 LLM 工具入参约束，不在 Runner 层 |
| IPC future 键语义（仅后端侧约束） | — | Runner 只被动响应 `execute_tool` 请求，不持有 future |
| 工具 schema 经 `get_tools` 上报 + `tools.sync` 推后端 | 对客户端（透传） | [PROTOCOL.md §2.2](../PROTOCOL.md) |
| **本地安全防线**：危险命令阻断 + Windows 路径不敏感写限制 + SSRF + Tirith 扫描 | 对本地工具执行 | [ARCHITECTURE.md §7](../ARCHITECTURE.md) |
| 工具三层分类中的 runner tools（需 IPC 下发） | — | 由后端决策，Runner 只接收 `execute_tool` |
| MCP 动态加载 + Skills frontmatter 平台过滤 | 本模块独有 | 本 README §2 |
| 音频引擎内置于基础 wheel | 本模块独有（[installer/README.md §2](../installer/README.md) 携带 Piper voices） | 本 README §2 |

## 6. 已知限制

| 限制 | 说明 |
|------|------|
| **TTY/stdin 不可用** | 所有 RPC 经本地 IPC 链路；任何 stdin 重定向或直接 console 输入都会与 C 库底层日志污染协议帧 |
