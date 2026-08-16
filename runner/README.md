# Runner

本地手脚——纯粹的工具执行器，承载伙伴"能帮用户做的事"。以 uv build wheel 形式发布，安装器在 `$SPIRITAGENT_HOME/runner/.venv` 创建 venv 并安装；Client 直接 spawn venv Python 调用 `server.py`，通过本地 OS IPC（Windows 命名管道 / macOS UDS，承载 WebSocket 帧）接收 JSON-RPC 2.0 工具调用指令并在用户机器上执行。

Runner 不感知"伙伴"语义——终端、文件、浏览器、代码执行等底层能力 100% 保留，伙伴人格完全由 Backend 承载、伙伴形象完全由 Client 渲染。

## 1. 职责与边界

**职责**：在用户本机执行 Backend LLM 决策调用的工具（终端 / 文件 / 浏览器 / 代码沙箱 / 系统感知 / 音频 / MCP 动态加载 / Skills）；按 `runner_ready` capabilities 上报本地 OS 能力；按需经反向 RPC 借 Client → Backend 调用 LLM。

**不**做：
- **不持有 Backend Token 或云端地址**——需借 LLM 时通过反向 RPC 由 Client 代理。
- **不装配对话提示词 / 不管理记忆 / 不调度对话**——这些是 Backend 责任。
- **不做 LLM provider 特定 schema 适配**（如折叠 `anyOf` null branch）——nullable union 原样传递，由目标 provider 决定能否接受。
- **不持久化伙伴状态**——所有状态都是 process-local。

架构层定位见 [ARCHITECTURE.md §1 / §2](../ARCHITECTURE.md)；Client ↔ Runner 协议见 [PROTOCOL.md §2](../PROTOCOL.md)；反向 RPC 桥接见 [PROTOCOL.md §3](../PROTOCOL.md)；安全防线见 [ARCHITECTURE.md §7](../ARCHITECTURE.md)。

## 2. 设计意图

- **剥离大脑逻辑**：系统提示词、provider 适配、对话记忆全部由 Backend 承载。Runner 是单纯的"接 JSON-RPC 工具调用 → 执行 → 返回结果"的执行器。
- **零凭证 / 无网络出站**：Runner 不保存任何用户 Token 或云端地址，无法直接访问 Backend。所有需 LLM 的工具走 `request_llm` 反向 RPC 经 Client 代为调用（[PROTOCOL.md §3](../PROTOCOL.md)）。**这是不可破坏的不变量**——即便 prompt 注入攻陷 Runner 工具逻辑，最坏情况也只是借 Client 调用受限用户账户下的 LLM，不会泄露 Backend 凭证。
- **Provider 范围**：产品 LLM 交互只面向 OpenAI-compatible providers，不接 Anthropic。nullable union 原样传递，由目标 provider 决定能否接受。
- **环境状态与工具解耦**：环境共享态（活跃实例表、工厂、cleanup 线程）下沉到 `tools/terminal/environment/` 子包，`file_tools` / `code_execution_tool` 跨包直接导入该子包、共享同一批 env 实例，绕开仍含命令处理 + 安全审批逻辑的 `terminal_tool` 避免循环依赖。
- **Capabilities 尽量运行时探测**：`utils.capabilities.snapshot()` 中 microphone（枚举 WASAPI/AVFoundation 设备）、screen_capture（mss 枚举监视器 / screencapture 存在性）、system_activity（真实调 `GetLastInputInfo` / `CGSessionCopyCurrentDictionary`）是运行时探测；local_stt / local_tts 是**执行原生加载器的 import 探测**（faster-whisper / piper 的 import 会加载 CTranslate2 / onnxruntime 二进制，失败即不可用）。不用 `find_spec` 存在性检查——那是欺骗 UI 让用户点不能用按钮。
- **音频引擎默认在基础 wheel 内**：`faster-whisper` / `piper-tts` / `sounddevice` / `numpy` 是伴侣语音栈的核心依赖（[DESIGN §7](../DESIGN.md)），从基础 wheel 直接可用。`pyttsx3` 用平台 marker 限制（macOS / Windows 上有 SAPI5 / NSSpeechSynthesizer 兜底）。运行时仍要求系统 PATH 有 `ffmpeg`（`audio_io.wav_to_wav_pcm16` 用）。
- **Skills 安全扫描的信任边界**：THREAT 扫描与结构检查对 community 来源强制执行；skill 自带的 `.skillignore` 只对 builtin/trusted 来源生效——不可信 skill 不能用自己的 ignore 文件关闭对自己的安全门禁。

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

**Wheel 产物**：`dist/spirit-agent-*.whl`。Client spawn `$SPIRITAGENT_HOME/runner/.venv/{bin/python,Scripts/python.exe} $SPIRITAGENT_HOME/runner/server.py --desktop-endpoint <path> --desktop-auth <token>`。安装布局详 [installer/README.md](../installer/README.md)。

## 4. 关键设计决策

- **OS IPC + WebSocket 帧而非 stdio 重定向**：Client 监听命名管道（Windows）/ UDS（macOS），Runner 主动连入，链路承载 WebSocket 帧协议。（1）避免 C 库底层日志或 Python `print` 污染 stdin/stdout 帧；（2）全双工并发，按 `id` 异步匹配响应，避免进程读写阻塞与全局锁；（3）零端口暴露——端点路径与每次启动的握手 token 由 Client 单向下发（启动参数 + `desktop-endpoint.json`），Runner 重连间重读文件跟随 Client 重启，401 拒绝即丢弃缓存端点等待新 token。**为什么不直接 stdin/stdout**：PTY/C 库会与控制台帧冲突；异步 RPC 也会被全局锁退化为串行。
- **`runner_ready` capabilities 字段运行时探测**：`utils.capabilities.snapshot()` 真正枚举设备、调底层 API；不依赖 `import` 是否成功。**为什么不用静态 extra 标记**：依赖可能在 import 时报警但运行时仍可用，反过来亦然；运行时探测才是真值。
- **环境共享态下沉到 `tools/terminal/environment/` 子包**：`file_tools` / `code_execution_tool` 跨包共享同一批 env 实例，绕开含命令处理 + 安全审批逻辑的 `terminal_tool` 避免循环依赖。
- **reverse RPC 速率守卫**：Client 转发前统计单会话请求次数与载荷大小（硬上限 200 帧 / 1MB），防止 Runner 工具逻辑失控刷爆 LLM 额度。**为什么是 Client 而非 Backend**：Client 是流量入口，能在 IPC 边界做最严格的拒绝。
- **Windows Job Object 内核级进程树生命周期绑定**：在 Windows 上 Runner 服务启动阶段（`server.py` 入口）显式将自身进程加入 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` Job Object，派生的所有子进程/孙进程/PTY 终端自动原子级继承该 Job；模块导入无隐式副作用，Runner 异常崩溃或被杀时由 Windows 内核原子强杀全进程树，杜绝孤儿进程悬挂。
- **Win32 `GetFinalPathNameByHandleW` 原生路径规范化**：针对 Windows 8.3 短文件名（`PROGRA~1`）、符号链接、目录联接点（Junction）及深层未创建子路径回溯解析真实路径；统一大小写不敏感比对、剥离 NT/UNC 设备前缀并拦截 NTFS 备用数据流（ADS）。
- **Tirith 扫描作为 shell 命令前置过滤器**：所有 shell 命令执行前拉起本地 `tirith` 模块对参数签名与静态审查，默认实行 Fail-Secure（`tirith_fail_open=False`），二进制未安装时友好降级并告警。**为什么不在 LLM 层做**：LLM 层做参数校验是治标；Tirith 在执行边界做拦截更彻底。
- **SSRF 在建连前 `getaddrinfo` + 建连时 httpx `event_hooks.connect` 双重校验**：仅 `getaddrinfo` 会被预检-建连之间的 DNS 重绑定绕过；httpx 的 connect 事件钩子捕获实际目标 IP 二次过滤。**为什么不只信任 URL 形式**：URL 形式不可信，`getaddrinfo` 后 host 可被重新解析。
- **`capabilities_health` 细粒度健康诊断与向后兼容**：`snapshot_with_health()` 同时产出平铺布尔字典与结构化 health 字典，记录各子能力（麦克风、屏幕截屏、本地 STT/TTS、系统活跃度）的探测可用性及具体失败原因（如设备缺失、依赖缺失），支持 Client 端按子功能精准优雅降级而非一刀切禁用。
- **`probe_failed` 独立于 capabilities 字段**：当 capabilities 探测整体抛致命异常时返回 `true`，Client 应当把这条 handshake 视为"功能状态不可信"，结合 `spiritagent.info` 进一步诊断。
- **依赖显式声明而非 try-except import**：Runner 以 uv wheel 分发、装到用户机器后依赖集即冻结、无法中途增补——所有 pip 依赖一律显式声明在 `pyproject.toml`（含平台 marker，如 `pywinpty; win32` / `ptyprocess; !=win32`），一个依赖"有就是有、没有就是没有"，不需要在导入时再判断。`try/except ImportError` 只允许两类合法场景：① 运行时能力探测（`capabilities.py` 故意执行原生加载器的 import 验证二进制真能加载，而非 `find_spec` 存在性检查）；② OS 框架/平台导入（`ctypes` / `Quartz` / `AppKit` / `pythoncom` 等非 pip 依赖）。对已声明依赖（`psutil` / `piper` / `faster-whisper` / `pyttsx3` / `mcp`）残留的 try-except 属历史遗留（ruff `F823` 的 "legacy try-imports" 佐证），方向是移除。
- **`execute_code` 沙箱 RPC 令牌鉴权**：每次代码执行生成一次性 Capability Token（env `SPIRITAGENT_RPC_TOKEN`），子进程首帧/请求文件校验；Windows loopback TCP 端点防范本地未授权进程访问。
- **单进程 1:1 架构模型**：多用户或多实例场景下每个 Client 单独 spawn 专属 Runner 进程，天然隔离各用户的本地权限、环境变量与进程上下文。

## 5. 与外部的契约

| 契约 | 方向 | 在哪定义 |
|------|------|---------|
| `runner_ready` payload（含 version + capabilities + capabilities_health） | 对 Client | [PROTOCOL.md §2.3](../PROTOCOL.md) |
| `spiritagent.info` 完整运行快照 | 对 Client | [PROTOCOL.md §2.2](../PROTOCOL.md) |
| RPC 方法清单（`runner_ready` / `get_tools` / `execute_tool` / `spiritagent.cancel` / `spiritagent.config.update` / `spiritagent.info` / `mcp.reload` / `request_llm` / `tools_changed`） | 对 Client | [PROTOCOL.md §2.2](../PROTOCOL.md) |
| 反向 RPC 桥接（`request_llm` → Client → `/api/llm/completion`） | 对 Client | [PROTOCOL.md §3](../PROTOCOL.md) |
| 反向 RPC 速率守卫（200 帧；文本 1MB / 视觉 10MB 上限） | 对 Client（Client 转发前限流） | [PROTOCOL.md §3](../PROTOCOL.md) |
| Reserved Keys 不适用 | — | Reserved Key 是 LLM 工具入参约束，不在 Runner 层 |
| IPC future 键语义（仅 Backend 侧约束） | — | Runner 只被动响应 `execute_tool` 请求，不持有 future |
| 工具 schema 经 `get_tools` 上报 + `tools.sync` 推 Backend | 对 Client（透传） | [PROTOCOL.md §2.2](../PROTOCOL.md) |
| **本地安全防线**：Hardline 危险命令阻断 + Windows 路径不敏感写限制 + SSRF + Tirith 扫描 | 对本地工具执行 | [ARCHITECTURE.md §7](../ARCHITECTURE.md) |
| 工具三层分类中的 runner tools（需 IPC 下发） | — | 由 Backend 决策，Runner 只接收 `execute_tool` |
| MCP 动态加载 + Skills frontmatter 平台过滤 | 本模块独有 | 本 README §2 |
| 音频引擎内置于基础 wheel | 本模块独有（[installer/README.md §2](../installer/README.md) 携带 Piper voices） | 本 README §2 |

## 6. 已知限制

| 限制 | 说明 |
|------|------|
| **TTY/stdin 不可用** | 所有 RPC 经本地 IPC 链路；任何 stdin 重定向或直接 console 输入都会与 C 库底层日志污染协议帧 |
