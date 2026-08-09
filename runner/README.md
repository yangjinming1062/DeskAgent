# Runner

本地手脚——纯粹的工具执行器，承载伙伴"能帮用户做的事"。以 uv build wheel 形式发布，安装器在 `$DESKAGENT_HOME/runner/.venv` 创建 venv 并安装；Client 直接 spawn venv Python 调用 `server.py`，通过 WebSocket 接收 JSON-RPC 2.0 工具调用指令并在用户机器上执行。

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
- **环境状态与工具解耦**：环境共享态（活跃实例表、工厂、cleanup 线程）下沉到 `tools/terminal/environment/` 子包，`file_tools` / `code_execution_tool` 跨包直接导入该子包、共享同一批 env 实例，绕开仍含命令处理 + 安全审批逻辑的 `terminal_tool` 避免循环依赖。`terminal/__init__.py` 对 `terminal_tool` 的重导出用 `__getattr__` 惰性加载。
- **Capabilities 由运行时探测而非 import 检查**：`utils.capabilities.snapshot()` 真正枚举设备、调底层 Win32 / Quartz / loginctl，没东西可答时才报 `False`。**不**退化为"import 是否存在"——那是欺骗 UI 让用户点不能用按钮。
- **音频引擎默认在基础 wheel 内**：`faster-whisper` / `piper-tts` / `sounddevice` / `numpy` 是伴侣语音栈的核心依赖（[DESIGN §7](../DESIGN.md)），从基础 wheel 直接可用。`pyttsx3` 用平台 marker 限制（macOS / Windows 上有 SAPI5 / NSSpeechSynthesizer 兜底）。运行时仍要求系统 PATH 有 `ffmpeg`（`audio_io.wav_to_wav_pcm16` 用）。

## 3. 架构地图

```
runner/
├── server.py              # 唯一 WebSocket 入口；分发所有 RPC 方法
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
└── utils/                 # 纯 helper 层：路径 / 配置 / 脱敏 / 文件安全 / PID / 反向 RPC / capabilities
```

依赖方向：`utils/` ← `tools/` ← `server.py`，`utils/` 不反向依赖任何工具；`tools/` 之间跨包直接 import 共享子包（如 `terminal/environment/`）。

**Wheel 产物**：`dist/deskagent-agent-*.whl`。Client spawn `$DESKAGENT_HOME/runner/.venv/{bin/python,Scripts/python.exe} $DESKAGENT_HOME/runner/server.py --desktop-ws <ws-url>`。安装布局详 [installer/README.md §10](../installer/README.md)。

## 4. 关键设计决策

- **WebSocket 而非 stdio 重定向**：（1）避免 C 库底层日志或 Python `print` 污染 stdin/stdout 帧；（2）全双工并发，按 `id` 异步匹配响应，避免进程读写阻塞与全局锁。**为什么不直接 stdin/stdout**：PTY/C 库会与控制台帧冲突；异步 RPC 也会被全局锁退化为串行。
- **`runner_ready` capabilities 字段运行时探测**：`utils.capabilities.snapshot()` 真正枚举设备、调底层 API；不依赖 `import` 是否成功。**为什么不用静态 extra 标记**：依赖可能在 import 时报警但运行时仍可用，反过来亦然；运行时探测才是真值。
- **环境共享态下沉到 `tools/terminal/environment/` 子包**：`file_tools` / `code_execution_tool` 跨包共享同一批 env 实例，绕开含命令处理 + 安全审批逻辑的 `terminal_tool` 避免循环依赖。`terminal/__init__.py` 用 `__getattr__` 惰性加载 `terminal_tool`，让 `import tools.terminal` 不触发循环环。
- **reverse RPC 速率守卫**：Client 转发前统计单会话请求次数与载荷大小（硬上限 200 帧 / 1MB），防止 Runner 工具逻辑失控刷爆 LLM 额度。**为什么是 Client 而非 Backend**：Client 是流量入口，能在 IPC 边界做最严格的拒绝。
- **PTY 在 Windows 上的 hardline 限制**：Windows PTY 进程链易悬挂（`runner/utils/pid.py` 提供 `psutil`/`taskkill` 原生封装）。**为什么不依赖 conpty**：conpty 在多进程链下挂掉后清理路径复杂；强制每条 PTY 命令走短时 taskkill 兜底。
- **Tirith 扫描作为 shell 命令前置过滤器**：所有 shell 命令执行前拉起本地 `tirith` 模块对参数签名与静态审查，命中危险模式立即阻断（不抛错给用户，仅 fail-fast 返回 ok=false）。**为什么不在 LLM 层做**：LLM 层做参数校验是治标；Tirith 在执行边界做拦截更彻底。
- **SSRF 在建连前 `getaddrinfo` + 建连时 httpx `event_hooks.connect` 双重校验**：仅 `getaddrinfo` 会被预检-建连之间的 DNS 重绑定绕过；httpx 的 connect 事件钩子捕获实际目标 IP 二次过滤。**为什么不只信任 URL 形式**：URL 形式不可信，`getaddrinfo` 后 host 可被重新解析。
- **`probe_failed` 独立于 capabilities 字段**：当 capabilities 探测整体抛异常时返回 `true`，Client 应当把这条 handshake 视为"功能状态不可信"，结合 `deskagent.info` 进一步诊断。**为什么不一起返回在 capabilities**：部分能力可能仍可用，整体失败应让 UI 降级而非禁用。

## 5. 与外部的契约

| 契约 | 方向 | 在哪定义 |
|------|------|---------|
| `runner_ready` payload（含 version + capabilities） | 对 Client | [PROTOCOL.md §2.3](../PROTOCOL.md) |
| `deskagent.info` 完整运行快照 | 对 Client | [PROTOCOL.md §2.2](../PROTOCOL.md) |
| RPC 方法清单（`runner_ready` / `get_tools` / `execute_tool` / `deskagent.info` / `mcp.reload` / `request_llm`） | 对 Client | [PROTOCOL.md §2.2](../PROTOCOL.md) |
| 反向 RPC 桥接（`request_llm` → Client → `/api/llm/completion`） | 对 Client | [PROTOCOL.md §3](../PROTOCOL.md) |
| 反向 RPC 速率守卫（200 帧 / 1MB 上限） | 对 Client（Client 转发前限流） | [PROTOCOL.md §3](../PROTOCOL.md) |
| Reserved Keys 不适用 | — | Reserved Key 是 LLM 工具入参约束，不在 Runner 层 |
| IPC future 键语义（仅 Backend 侧约束） | — | Runner 只被动响应 `execute_tool` 请求，不持有 future |
| 工具 schema 经 `get_tools` 上报 + `tools.sync` 推 Backend | 对 Client（透传） | [PROTOCOL.md §2.2](../PROTOCOL.md) |
| **本地安全防线**：Hardline 危险命令阻断 + Windows 路径不敏感写限制 + SSRF + Tirith 扫描 | 对本地工具执行 | [ARCHITECTURE.md §7](../ARCHITECTURE.md) |
| 工具三层分类中的 runner tools（需 IPC 下发） | — | 由 Backend 决策，Runner 只接收 `execute_tool` |
| MCP 动态加载 + Skills frontmatter 平台过滤 | 本模块独有 | 本 README §2 |
| 音频引擎内置于基础 wheel | 本模块独有（[installer/README.md §11](../installer/README.md) 携带 Piper voices） | 本 README §2 |

## 6. 已知限制

| 限制 | 说明 |
|------|------|
| **Windows PTY 兼容性是已知风险面** | 进程链悬挂需用 `utils/pid.py` 的 `psutil`/`taskkill` 原生封装；conpty 在多进程链下清理路径复杂 |
| **并行 terminal 不可用** | Runner 端共享 LocalEnvironment 实例，快照文件不可并发写；架构决定 |
| **音频引擎需 ffmpeg** | 运行时仍要求系统 PATH 有 `ffmpeg`（`audio_io.wav_to_wav_pcm16` 用）；非零配置门槛 |
| **MIMO 设计音色不支持本地回退** | voice_id 编码为 `mimo_voicedesign:<prompt>` 自描述 token，Piper 解析不动——路由到 cloud 失败时即 TTS 整体失败 |
| **`request_llm` 反向 RPC 速率硬上限** | 200 帧 / 1MB 每会话，超限由 Client 拒绝；防 Runner 工具逻辑失控刷爆 LLM 额度，但**也可能误伤**正常高频工具（如高频率视觉理解） |
| **TTY/stdin 不可用** | 所有 RPC 经 WS；任何 stdin 重定向或直接 console 输入都会与 C 库底层日志污染 WS 帧 |
| **单进程架构** | Runner 不支持水平扩展；多用户场景下每个 Client 单独 spawn 独立 Runner 进程 |
| **`probe_failed` 时 UI 降级需手动** | 部分能力可能仍可用，但 Client UI 收到 `probe_failed=true` 时整体降级；当前没有更细粒度的子能力独立上报 |