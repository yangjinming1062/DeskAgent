# Runner

本地手脚——纯粹的工具执行器，承载伙伴"能帮用户做的事"。以 uv build wheel 形式发布，安装器在 `$DESKAGENT_HOME/runner/.venv` 创建 venv 并安装；Client 直接 spawn venv Python 调用 `server.py`，通过 WebSocket 接收 JSON-RPC 2.0 工具调用指令并在用户机器上执行。Runner 不感知"伙伴"语义——终端、文件、浏览器、代码执行等底层能力 100% 保留，伙伴人格完全由 Backend 承载、伙伴形象完全由 Client 渲染。

设计文档：[ARCHITECTURE.md](../ARCHITECTURE.md) §2 / §3 / §4 / §7

## 设计意图

- **剥离大脑逻辑**：系统提示词、多模型适配器、对话记忆模块全部由 Backend 承载。
- **剔除网络请求**：Runner 不保存任何用户 Token 或云端地址，无法直接访问 Backend。需借 LLM 时通过反向 RPC 请求 Client 代为调用（[ARCHITECTURE.md §4.2.II](../ARCHITECTURE.md)）。
- **Provider 范围**：产品 LLM 交互只面向 OpenAI-compatible providers，不接 Anthropic。Runner 不做 LLM provider 特定的 schema 适配（如折叠 `anyOf` null branch）——nullable union 原样传递，由目标 provider 决定能否接受。
- **环境状态与工具解耦**：环境共享态（活跃实例表、工厂、cleanup 线程）下沉到 `tools/terminal/environment/` 子包，`file_tools`、`code_execution_tool` 跨包直接导入该子包、共享同一批 env 实例，绕开仍含命令处理 / 安全审批逻辑的 `terminal_tool` 避免循环依赖。`terminal/__init__.py` 对 `terminal_tool` 的重导出用 `__getattr__` 惰性加载——包初始化时 terminal_tool → files → environment → `terminal/__init__` → terminal_tool 的环不会触发。

## 架构地图

- `server.py` 是唯一 WebSocket 入口——所有 RPC 方法（`runner_ready` / `get_tools` / `execute_tool` / `deskagent.info` / `mcp.reload` / `request_llm`）都在此分发，不暴露其他网络端口。
- `tools/` 各子包（`terminal` / `files` / `browser` / `execute_code` / `process` / `skills` / `mcp` / `multimodal` / `system` / `toolsets` / `security`）在 import 时自注册到 `registry`，`server.py` 不感知具体工具。
- `tools/terminal/environment/` 子包下沉共享态（活跃实例表 / 工厂 / cleanup reaper），让 `files`、`execute_code` 跨包共享 env 实例、绕开含命令处理 + 安全审批的 `terminal_tool` 避免循环依赖。
- `utils/` 是无业务逻辑的纯 helper 层：路径解析 / 配置 / 脱敏 / 文件安全 / PID / 反向 RPC / capabilities 探测——`tools/` 与 `server.py` 都依赖它，它不反向依赖任何工具。

Wheel 产物：`dist/deskagent-agent-*.whl`。Client spawn `$DESKAGENT_HOME/runner/.venv/{bin/python,Scripts/python.exe} $DESKAGENT_HOME/runner/server.py --desktop-ws <ws-url>`。安装布局详 [installer/README.md §10](../installer/README.md)。

**音频引擎默认在基础 wheel 内**：`faster-whisper` / `piper-tts` / `sounddevice` / `numpy` 是伴侣语音栈的核心依赖（[DESIGN §5 语音交互](../DESIGN.md#5-语音交互stt--tts)），从基础 wheel 直接可用。`pyttsx3` 用平台 marker 限制（macOS / Windows 上有 SAPI5 / NSSpeechSynthesizer 兜底）。运行时仍要求系统 PATH 有 `ffmpeg`（`audio_io.wav_to_wav_pcm16` 用）。

要确认 capability 是否在当前 venv 内为真，调 `deskagent.info` 看 `capabilities.local_stt / local_tts` 字段——运行时检测比静态 extra 标记更准（依赖可能在 import 时报警但运行时仍可用，反过来亦然）。

## 通信协议

Runner 主动连接 Client 提供的本地 WS 服务器（`ws://127.0.0.1:<port>/rpc`），启动参数 `--desktop-ws`。连接后发送 `runner_ready` 握手通知，**payload 含 runner 版本与 `capabilities` 探测快照**——Client 据此在握手阶段决定是否暴露语音通话 / 唤醒词 / 主动陪伴等依赖 OS 能力的功能（plan §5.1 / §6）。

### `runner_ready` payload

```json
{
  "version": "0.2.0",
  "capabilities": {
    "microphone": false,
    "screen_capture": true,
    "local_stt": false,
    "local_tts": false,
    "system_activity": true,
    "platform": "win32",
    "python": "3.11.13"
  },
  "probe_failed": false
}
```

`capabilities.local_stt` / `local_tts` 由 `utils.capabilities.snapshot()` 计算——`microphone_available` 真去枚举设备（Win/Mac 走 `sounddevice.query_devices`），`system_activity_available` 真调底层 Win32 / Quartz / loginctl，没东西可答时才报 `False`。**不要把 capability 探测退化成"import 是否存在"——那是欺骗 UI 让用户点不能用按钮。** 语音通话 / 唤醒词在 capability 为 `false` 时被 Client 静默隐藏，伴侣不强提示。

`probe_failed` 字段：正常为 `false`；等于 `true` 表示 capabilities 探测整体抛异常（外部依赖损坏、Cap' 探测死循环等）——Client 应当把这条 handshake 视为"功能状态不可信"，结合 `deskagent.info` 进一步诊断。

### `deskagent.info`（动态查询）

任意时候调 `{"method": "deskagent.info"}` 返回完整进程快照，便于 Client 诊断面板与故障态降级（plan §5.5）：

```json
{
  "version": "0.2.0",
  "started_at": 1722345678.901,
  "uptime_seconds": 1842.31,
  "reconnect_count": 0,
  "capabilities": { ... 上面 payload 同 ... },
  "system": { "platform": "win32", "python": "3.11.13", "release": "...", "machine": "AMD64" },
  "tool_count": 53,
  "mcp_servers": ["github", "filesystem"],
  "network_reachable": true,
  "disk_free_bytes": 524288000000
}
```

### RPC 方法清单

| 方法 | 方向 | 用途 |
|------|------|------|
| `runner_ready` | Runner → Client | 启动握手通知；携带 `version` + `capabilities` |
| `tools_changed` | Runner → Client | 工具 schema 变更通知（MCP 后台发现完成后触发）；Client 收到后重拉 `get_tools` 并重新 `tools.sync` 到 backend |
| `get_tools` | Client → Runner | 获取工具 Schema（已过滤：toolset disabled & capability check 失败的工具均不返回） |
| `deskagent.info` | Client → Runner | 返回完整运行快照，见上 |
| `execute_tool` | Client → Runner | 执行工具调用 |
| `mcp.reload` | Client → Runner | 第一类 RPC（不走 `execute_tool`）：关闭当前所有 MCP 连接并从最新 `$DESKAGENT_HOME/config.yaml` 重新连接；同时清 `tool_output_limits` / `file_read_max_chars` 缓存，让相关 config 改动免重启生效。无入参（runner 始终读本地 YAML） |
| `deskagent.cancel` | Client → Runner | 中断信号：设 `_global_interrupt` 让 in-flight 工具下次轮询时退出 |
| `request_llm` | Runner → Client | 反向 RPC（带 `id` 的请求）：借用 LLM，响应体可含 `content` / `choices[0].message.content` / `text`，`server.py::_extract_llm_content` 做容错抽取 |

**自动退出与重连**：WS 断开后进入重连循环（指数退避 2s → 30s，最多 15 次约 5 分钟）。每次重试前读 `$DESKAGENT_HOME/desktop-endpoint.json` 获取最新端口（Client 重启后端口变化），并检查 Client PID 存活以跳过残留文件。超时后 `sys.exit(1)`。

## 工具系统

### 注册协议

每个工具模块在 import 时调用 `registry.register_tool(...)` 完成注册。`discover_builtin_tools()` 递归扫描 `tools/` 子包（跳过 `registry` 和 `mcp/mcp_tool`——MCP 模块的特性见下文）。

工具按类别分组（具体工具名 grep `register_tool` 即可得）：终端 / 文件 / 浏览器（`browser_*` 系列）/ 代码执行 / 进程 / Skills / 多模态（视觉 + 音频）/ 系统感知（`system.*`）/ MCP（运行时动态发现，每个 server 附带 4 个 utility：list / read resources、list / get prompts）。

**Handler 契约**：接收 `**kwargs`，返回 JSON 字符串。

**Schema 要求**：每个工具必须提供显式 JSON Schema。Backend 依赖这些 schema 告知 LLM 工具参数。Runner 不做 provider-specific 适配。

**Capability 门控（`check_fn`）**：工具可以声明一个 zero-arg 探测函数（`check_fn`），TTL 缓存 30 s；探测失败 60 s 内仍保留 last-good 结果（hermes-agent 模式），防止瞬态失败把工具从 schema 抹掉。`get_tools` RPC 返回的 schema 已自动过滤 `check_fn is False` 的工具——LLM 永远看不到此刻跑不起来的工具。示例：`speech_to_text` 在 `faster-whisper` 未装的 venv 里直接消失；`text_to_speech` 在 Piper/pyttsx3 都缺的环境里消失。

**结果规范**：成功 `tool_result(...)`、失败 `tool_error(...)`，均返回 JSON 字符串。清洗流水线：`ansi_strip → strip_fence → redact`。大结果持久化到文件，返回 `<persisted-output>` 标签。

### MCP 动态工具发现

MCP 工具由 `discover_mcp_tools()` 在 `server_loop` 紧跟 `runner_ready` 之后**后台线程**调用，从 `$DESKAGENT_HOME/config.yaml` 的 `mcp_servers` 段读取配置、连接各 server、把 `mcp_<server>_<tool>` 注册到全局 registry。**这是 MCP 工具进入 backend LLM schema 的唯一入口**。

后台执行是为了让 bridge 握手在 <1s 完成——desktop 立刻收到 `runner_ready` 并 `get_tools` + `tools.sync` 静态工具；MCP 发现完成后 runner 发 `tools_changed`，desktop 重拉 + 重 `tools.sync`，LLM 在下一轮 turn 看到 MCP 工具。运行时新增/删除 MCP server 须经 Runner 重启（Client MCP 设置页 `runnerConfig.write` 走 `restartRunnerBridge`）才能让 backend 看到。

**MCP server 通知处理**：Runner 在 MCP `ClientSession` 上注册的 message handler 仅消费 `ToolListChangedNotification`——收到时重新拉取 tool 列表并热替换 registry 条目。`PromptListChangedNotification` 与 `ResourceListChangedNotification` 仅 debug 日志后被忽略（**设计意图而非 TODO**）：list-change 不触发热刷新，要看新增/删除的 prompt/resource 须经 Client 主动触发 `mcp.reload`，不能依赖 Runner 内的自动响应。

**Prompt / Resource 经 utility 工具暴露**：每个 MCP server 的 resources 和 prompts 注册为 `mcp_<server>_list_resources` / `_read_resource` / `_list_prompts` / `_get_prompt` 四个 utility 工具，与普通 MCP 工具一起进 LLM schema。双重门控：(1) server 在 initialize 握手声明了对应 capability（以 `initialize_result.capabilities` 为准，不靠 `hasattr(session, ...)`——`ClientSession` 总是定义这四个方法属性，旧判据从不过滤）；(2) config `mcp_servers.<name>.tools.resources` / `.prompts` 未显式关闭（默认开）。utility 工具同样走 `mcp_` 前缀、受同名 collision guard 保护。

**Schema 修复是 MCP 协议层而非 LLM 适配层**：`tools/mcp/mcp_tool.py` 内部的 `_rewrite_local_refs` / `_repair_object_shape` 是 MCP 协议本身需要的结构修复（处理 `$ref`、补 `type` 字段等），与目标 LLM provider 无关。

### 音频工具 (STT + TTS)

为伴侣语音能力 ([DESIGN §5](../DESIGN.md#5-语音交互stt--tts)) 提供零成本主路径。Runner 本地引擎（faster-whisper STT、Piper/pyttsx3 TTS）是默认主路径；Client 在本地不可用或失败时回退 Backend 云端引擎（`/api/media/stt|tts`），三档可切（`auto` 本地优先 / `local` 纯本地 / `cloud` 强制云端）。

**`speech_to_text`**（`tools/multimodal/audio/stt_tool.py`）：接收本地路径 `audio_path` 或 base64 编码的音频字节（≤ 25 MB，mp3/wav/m4a/ogg/flac/webm/aac）；先经 ffmpeg 解码到 16 kHz mono PCM16 WAV，再喂给 faster-whisper（CTranslate2）。语言自动检测或显式传 `language`；模型大小 `tiny | base | small | medium | large-v2 | large-v3`（默认 `base`，模型文件按需下载到 `$DESKAGENT_HOME/models/whisper/`）。Segment-confidence gate 丢弃 `no_speech_prob > 0.6` 或 `avg_logprob < -1.0` 的段。返回 `{success, text, language, language_probability, segments[]}`。`check_fn` 探测 `faster-whisper` 是否可导入——未装时从 LLM schema 自动消失。

**STT auto-detect + low-confidence cloud fallback**：
- `language` 字段接受 `None` / `""` / `"auto"` 任意一种 → runner 走 whisper auto-detect；`language="zh"` / `"en"` 等显式值则让 whisper 在该语言上做识别（固定模型）。
- Auto-detect 模式下，runner 检查 `language_probability`（whisper 对 auto-detect 出的语言的置信度）。如果 < 0.5（明确"whisper 自己都不知道我说的是什么"）或全部段被 confidence gate 过滤掉（text 为空），runner 返 `tool_error` + `hint="Set stt.engine=cloud in config.yaml"`。desktop IPC 层（`engine_pref='auto'`）的现有 local→cloud fallback 接管——自动改跑云端 STT 而不是让用户对着空文本框发呆。
- 显式 `language='zh'` 等不走这个检查：用户说"按中文识别"，whisper 怎么不确信都返结果，避免强行 cloud fallback 让用户多付一份 API 费。

**`text_to_speech`**（`tools/multimodal/audio/tts_tool.py`）：本地优先 Piper（44 语言 VITS、CPU 实时），不可用时降级 `pyttsx3`（系统 TTS SAPI5/NSSpeech/espeak）。输入 `(text, voice?, engine?, speed)`；`speed` 钳制到 `[0.5, 2.0]`；文本预处理剥离 `<!-- 注释 -->` 与 `<!-- think -->` 块。输出写入 `$DESKAGENT_HOME/cache/audio/tts/{hint}_{uuid}.wav`，返回 `{success, path, engine, voice, size_bytes}`。Client 渲染层拿到路径后由其 `<audio>` 播放——Runner 不做音频回放。

**本地 TTS voice 选型**（参考 [DESIGN §5 语音交互](../DESIGN.md#5-语音交互stt--tts)）：

- **仅支持中英文两种语言，默认中文**。产品定位决定 TTS 永远以"中文为系统身份"——onboarding silhouette、默认 TTS 反馈都走中文 Piper voice。LLM 偶尔返回的英文回复也用中文 voice 念（不会"自动切到 EN voice"导致产品身份不稳定）。用户在 voice catalog 显式挑了 EN voice（`en_US-amy-medium`）时例外，desktop 把 id 透传到 runner。
- **Auto 模式本地失败 fallback**（`tts_tool.py::text_to_speech_tool`）：默认 `engine=auto` 走 Piper → pyttsx3 链。任一引擎合成失败（model corruption / OOM / FileNotFoundError 中途丢文件）自动静默回退下一引擎——避免 onboarding 期间 Piper 模型坏掉直接给出"声音不可用"的硬错误。链路耗尽才返 `tool_error("all engines failed: …", hint="Set tts.engine=cloud in config.yaml")`，告诉用户把 TTS 切到云端是最终逃生口。显式 `engine=piper` / `engine=pyttsx3` 走 single-engine 路径（pyttsx3 失败不偷偷回退 Piper，反之亦然），方便测试和强制走某条路径。
- **STT 默认 `language="zh"`**（`client/main/ipc/media.cjs`）：product direction 同样适用——renderer 调 `media.stt` 时不传 `language` 字段，IPC 层在缺省值处默认填 `zh`（随 `form.set('language', 'zh')` 一并 POST 到 backend `/api/media/stt`），让 faster-whisper 命中中文模型——避免对中文语音输入每次都做 auto-detect。要回退到 auto-detect 时 renderer 显式传 `language: 'auto'` 即可（IPC 层 `if (typeof payload?.language === 'string' && payload.language)` 守门）。
- **Voice 解析**（`piper_runtime.py::pick_voice_for_text`）：caller 显式 `voice` 参数永远胜出；缺省时**永远返回 `ZH_DEFAULT_VOICE`**（`zh_CN-huayan-medium`），不按文本语言路由。
- **Bundled voices**（`piper_runtime.py::bundled_voices`）：install payload 在 `installer/payload/voices/` 下带三份 Piper voice：`zh_CN-huayan-medium`（女声，默认）、`zh_CN-chaowen-medium`（男声备用）、`en_US-amy-medium`（用户从 catalog 选 EN 时使用）。`install.{sh,ps1}` 在 `unpack-runner` stage 把所有 onnx/json 拷到 `$DESKAGENT_HOME/models/piper/`。新增 voice 只需把 onnx/json 投入 payload 目录并把 id 加到 `_BUNDLED_VOICES` 元组，无需改 install 脚本（content-based copy）。
- **Auto-download fallback**（`piper_runtime.py::ensure_voice_installed`）：若请求的 Piper voice 不在磁盘且没在 bundled 列表里，会从 `huggingface.co/rhasspy/piper-voices` 拉一次（`zh_<region>-<name>-<quality>` 自动解析到 `zh/zh_CN/huayan/medium/...` 的 repo 路径）。失败返回 `False`、回退 pyttsx3，绝不抛错阻断 speak。
- **Cloud voice id 拦截**：用户传了云端 id（`mimo_default` / `冰糖` / `Mia` / `female-shaonv` 等）时本地工具不静默兜底，直接返回 `tool_error` 并提示 desktop 切到 `tts.engine=cloud`——避免把云端 id 喂给 Piper 然后听到 HUAYAN 这种诡异结果。判据：id 不匹配 Piper 的 `lang_REGION-name-quality` 形态（`piper_runtime.py::PIPER_VOICE_RE`）就视为云端 id。
- **Pyttsx3 中文 voice**：在 Windows 上 `_enumerate_pyttsx3_voices` 拉 SAPI5 voice 列表，对 CJK 文本优先选 `name`/`languages` 含 "Chinese" / "中文" / "Mandarin" 的 voice；枚举失败回退系统默认。
- **STT（Whisper）**：client 端 `media.stt` IPC 在 `language` 缺省时默认填 `zh`（[media.cjs#deskagent:media:stt](../client/main/ipc/media.cjs)），POST 到 backend `/api/media/stt` 的 form 字段里——让 faster-whisper 命中中文模型。要 auto-detect 时 renderer 显式传 `language: 'auto'` 即可。`zh_CN-huayan-medium` 等中文 voice 的 22050Hz 采样率与 Whisper 16kHz mono 输入不冲突——前者只用于 TTS 输出。

**`list_tts_voices`**：枚举 `$DESKAGENT_HOME/models/piper/` 下所有已下载的 Piper 语音（同时存在 `.onnx` 与 `.onnx.json` 才算安装完整）。

**模型 & 引擎装入策略**：audio deps（`faster-whisper` / `piper-tts` / `pyttsx3` / `sounddevice`）在基础 wheel 内（伴侣语音是核心能力，不再是 optional extra）；运行时仍要求系统 PATH 有 `ffmpeg`。Capability 探测在握手时验 `import faster_whisper` / `import piper` / `import pyttsx3`，无任一可导入即 `runner_ready.capabilities.local_stt|local_tts=false`，Client 隐藏语音相关 UI。

### 环境感知 (`system.*`)

驱动 [DESIGN §3 空间行为](../DESIGN.md#3-空间行为与移动)（perch / roam / ritual walk）、[§5.4 情境动作](../DESIGN.md) 与 [§6.2 打扰档位](../DESIGN.md)。Client 用 `setInterval` 轮询这些工具（标准 `execute_tool` 通道），结果完全脱离 LLM 直接进状态机判定——这不是 LLM 工具，是采样探针。

每个 probe **失败返回 safe default**（`-1.0` / `False` / `{}`）——错的"已锁屏"信号会直接静默伴侣，不接受。

`system.get_focused_app` 的返回值包含焦点窗口几何 `{x, y, w, h}`（各平台通过 `GetWindowRect` / `CGWindowBounds` / `wmctrl -lG` 获取），Client 用于计算 perch 位。`system.get_windows` 枚举全部可见顶层窗口（含几何 + focused 标记），供 roam 自由空间判定与 ritual walk 目标解析。`system.open_application` 是 LLM 可调工具（非探针），Client 在 events.ts 拦截它以触发 ritual walk——执行工具后按名称匹配窗口、精灵飞到目标旁播放 INTERACTING 动画。三个工具均无 toolset 条目，永远对 LLM 可见。

### computer_use 增强

`tools/multimodal/cu_tool.py` 在原有 14 个 action 上扩展了两个动作参数 + 一个稳定取消前缀：

- **`delivery_mode: background | foreground`**（默认 `background`）：与 hermes-agent 的 `(action, delivery_mode)` 鉴权 scope 对齐——foreground 模式需要单独的鉴权 scope，与 background 互不传染。Runner 不强制鉴权，但把 `delivery_mode` 写入 `ActionResult` 让上游 / LLM 读到。
- **`bring_to_front: bool`**（默认 `false`）：仅 focus_app 实际起作用——与 `raise_window` 同义（兼容旧 schema）；其他 action 接受但忽略。
- **`INTERRUPTED_PREFIX = "[INTERRUPTED]"`**：当 `is_interrupted()` 命中时，cancel 响应 JSON 里加 `prefix` 字段与 `returncode: 130`——ACP / TUI 客户端可以稳定前缀匹配判断"这回合是干净的取消"，避免把取消词误当成助手输出。
- **`ActionResult` 新增字段**（hermes-agent `_classify_action_result` 模式）：`verified` / `effect` / `escalation` (`done | verify_fresh_state | escalate`) / `path` / `code` / `delivery_mode` —— `_text_response` 自动序列化其中非空项。

### 浏览器 toolset check_fn

`browser_tool.py` 注册的 `browser_*` 工具挂 `check_fn=_browser_check_fn`（委托给 `check_browser_requirements()`：Camofox 模式 / CDP 模式 / 本地 agent-browser + Chromium 三选一）。浏览器子系统整个未装时，这些工具从 LLM schema 消失——避免 LLM 在没有浏览器的容器里尝试触发无谓的 RPC。`browser_dialog_tool.py` / `browser_cookie_tool.py` / `browser_cdp_tool.py` 注册的工具（dialog / cookies / storage / cdp）不带 check_fn。

### 测试钉子

`tests/` 分四层：WebSocket 协议层（真起 `websockets.serve` 跑 `runner_loop`）/ process & pid 跨平台层 / utils 残留覆盖（file_safety、redact、config、env_helpers）/ tool 子包纯 helper。

`test_startup_imports.py` 钉死 `server.py` 的每行 module-level import（MCP load-bearing 等关键传递依赖）。`.pre-commit-config.yaml` 在 runner 文件改动时跑 startup 子集（<1s）；`build_client.{ps1,sh}` 在 `uv build --wheel` 之前跑整个 `tests/` 作为发布门——任意一层失败都拦下坏 wheel（env-rot、传递依赖损坏永远不应该出 repo）。

## 终端后端

执行后端在 `tools/terminal/_env_*.py`：`_env_base` 抽象类 + `_env_file_sync` 同步 helper + 4 个后端（`_env_local` PTY/Pipe、`_env_docker`、`_env_ssh` ControlMaster、`_env_singularity`/Apptainer）。进程级共享态与生命周期在 `environment/` 子包：`state.py` 持有 `_active_environments` / `_last_activity`，`factory.py` 按 `env_type` 分发，`cleanup.py` 跑后台 reaper。

**环境缓存与隔离**：env 实例按 `task_id` 缓存。`resolve_container_task_id` 把无镜像 override（`docker_image` / `singularity_image` / `env_type`）的 task 折叠到 `"default"`——local 默认下所有 task 共享同一 LocalEnvironment；带 override 的 task 拿到独立容器。docker/singularity 支持 persistent 容器（`container_persistent` 跨命令、`docker_persist_across_processes` 跨 runner 进程存活），reaper 按 `lifetime_seconds` 回收孤儿容器。

**命令执行模型**：spawn-per-call + 会话快照——每条命令起新 bash，先 source 快照恢复 exports/functions/aliases，执行后写回；CWD 用 marker-based stdout 提取。并行 terminal 不安全且未被锁保护：同一 env 实例的快照/CWD 文件不可并发写，默认 local 共享实例意味着并发命令互相覆盖——这是真约束，调用方须自行串行化。

## 安全机制

### 危险命令审批

关键不变量：**Hardline blocklist 永远生效，YOLO 模式不可绕**——`force=True`（YOLO）仅跳过非硬阻断项。`SUDO_PASSWORD` 缺失时拒绝 `sudo -S`（防密码猜测）。

### 路径安全（`utils/file_safety.py`）

写拒绝列表：SSH 密钥、AWS/GCP/Kube 凭据、OAuth token、shell 配置、/etc/sudoers、/etc/passwd、/etc/shadow。

**Windows 路径大小写与设备前缀归一化**：`is_write_denied` 与 `get_read_block_error` 先经过 `_resolve_long_path` 规范化——自动剥离 `\\?\UNC\`（转换为 `\\`）、`\\?\` 与 `\\.\` 设备路径前缀并展开 8.3 短名称（如 `PROGRA~1`），再对**两侧**路径做 `replace("\\", "/").lower()` 归一化后比对——`\\\\?\\C:\\Windows\\System32` / `C:\Windows\System32` / `c:/windows/system32` / `C:/WINDOWS\System32\` 都命中同一前缀。POSIX 路径**不**做大小写折叠。`get_windows_sensitive_prefixes()` 经 `winapi.GetLogicalDrives` 枚举**所有**挂载盘符（不止 `C:`），所以 `D:\Windows\...`（Windows 装在 D: 的企业镜像）或网络共享里嵌的 `Windows` 目录树同样被拦截。

### 跨 profile / 跨沙箱镜像写检测

三种 cross-scope 写检测走同一条 soft-guard 路径：

| 检测器 | 命中的路径形状 | 默认行为 |
|--------|----------------|----------|
| `cross-profile` | 另一 profile 的 `skills/plugins/cron/memories` 目录 | 软警告，agent 可经 `cross_profile=True` opt-in（需用户明确许可） |
| `sandbox-mirror` | host-side `…/sandboxes/<backend>/<task>/home/.deskagent/…`（Docker / Daytona 等非本地后端的绑定镜像） | 软警告 |
| `container-mirror` | Docker 容器内部去前缀后的 `/root/.deskagent/…` 路径 | 软警告 |

设计意图：**soft-guard 而非 hard block**——同一 OS 用户下，agent 通过 terminal 工具本身就能写到任何路径，所以硬阻断只会给"虚假的安心感"。soft-guard 让 agent 在 LLM 提示层看到警告，必须先获得用户 `cross_profile=True` 才能覆盖。三个检测器共享同一道 opt-in 闸门，不是每个 detector 各自一份 override。

### URL 安全（`utils/url_safety.py`）

SSRF 防护：block private IPs、loopback、link-local、CGNAT（100.64.0.0/10）、云 metadata（169.254.169.254、metadata.google.internal）。DNS 解析校验。

### Tirith 安全扫描

每次终端命令执行 `tirith check --json --non-interactive --shell posix`。**Fail-open**（`tirith_fail_open` config flag，默认 True）——tirith 不可用时放行而非阻断，避免安全工具成为单点故障。tirith 二进制自动安装（cosign 验证 + SHA-256）。

### Secret 脱敏（`utils/redact.py`）

正则匹配 API keys（sk-*、ghp_*、AKIA* 等）、JWT、连接字符串、私钥。结果返回前统一脱敏。

### execute_code 沙箱

环境变量清洗：前缀白名单 + secret 关键字（KEY/TOKEN/SECRET/PASSWORD…）黑名单双过滤，各工具可经 `register_env_passthrough` 运行时追加放行项；Windows 额外放行系统必需变量（SYSTEMROOT/WINDIR 等）。工具调用限制 50 次/脚本。5 分钟超时。50KB stdout 上限。

### MCP OSV 检查

启动 stdio MCP server 前查询 OSV API 的 MAL-* advisory。

## Skills 系统

Skills 由安装器 seed 到 `$DESKAGENT_HOME/skills/`。启用/禁用配置在 `$DESKAGENT_HOME/config.yaml::skills.disabled`，由 Desktop（`deskagent:skill:set-enabled` IPC）写入、Runner（`get_disabled_skill_names`）读取；category-grain 匹配，单条 entry 覆盖整个 category 下的所有 leaf。

**路径单一来源**：所有 skills 模块都通过 `utils.get_skills_dir()` 解析运行时 skills 路径，不允许再各自 `DESKAGENT_HOME / "skills"`。新增 skill 模块前先 `from utils import get_skills_dir`。

**平台过滤**：`skills_tool.py::skill_matches_platform` 将 SKILL.md frontmatter 的 `platforms`（`macos` / `windows`，加历史 `linux` 别名）通过 `_PLATFORM_MAP` 翻译成 `sys.platform` 字符串（`darwin` / `win32`）再比较。两套字符串不能直接对比——必须走 `_PLATFORM_MAP` 翻译。Client `lib/skill-index.cjs` 在 main 进程同样做翻译；两套表结构不同但语义对齐，新增平台时需同步更新两边。

## 工具集系统（Toolsets）

Toolsets 是用户可平移的 LLM-facing schema 过滤单位。Catalog 是**三个镜像**的真相（Client TS / Client CJS / Runner Python），同提交同步。

启用/禁用配置在 `$DESKAGENT_HOME/config.yaml::toolsets.disabled`（Client IPC 写、Runner `toolsets/helpers.py::get_disabled_toolset_ids` 读）。过滤在 `registry.get_schemas_for_llm(set_of_disabled_ids)` 生效——被禁工具集对应 catalog 中的 `prefixes` 项扫实际注册表的 tool 名命中即排除；`extra_tools` 按名命中即排除；**MCP 工具（`mcp_*`）无条件排除**（它们的 toggle 在 MCP settings 页）。

**与 backend CORE_TOOLS 的分层语义**：`backend/services/chat/types.py::CORE_TOOLS` 是 Backend 侧的**硬保证**白名单；工具集过滤在更上游（Runner `get_tools` RPC），是**软补充**。两层语义独立。

**重启语义**：`deskagent:toolset:set-enabled` 走 atomic write + `restartRunnerBridge()`——Registry 是一次性 init，没有热重载入口，进程级 restart 是唯一路径。

## 浏览器多后端

支持的后端仅三种：Local headless Chromium（`agent-browser` CLI）、Camofox 远程反检测浏览器、用户自供的 CDP endpoint。不支持 Browserbase / Browser-Use v3 / Firecrawl 这类云浏览器 provider。若未来要恢复云浏览器能力，需同时引入 provider 注册 + 凭证管理 + `browser_*` 工具 routing 逻辑。

CDP Supervisor（`browser_supervisor.py`）：持久 WebSocket 到 CDP，dialog 拦截（Fetch domain 注入 JS bridge）、frame tree 跟踪、console 监控。持久化 profile（cookie + storage 跨重启存活）经 `profile_manager.py` 管理——解析 `$DESKAGENT_HOME/browser_profiles/<name>`，检查锁冲突，后台 cleanup 线程每 30s 跑 72h 保留 GC。

## 已知限制

### 平台陷阱（Windows）

| 问题 | 影响 | 缓解 |
|------|------|------|
| `os.kill(pid, 0)` 在 Windows 实际终止进程 | 存活检查会误杀进程 | 统一走 `utils/pid.pid_exists()`，Windows 上经 `psutil.pid_exists()` |
| `psutil.children(recursive=True)` PPID 链在 Windows 易过期 | 子进程树可能漏杀 | `utils/pid.kill_tree()` 统一在 Windows 上 `taskkill /T [/F]` |
| `subprocess.Popen` 在 Windows 需隐藏控制台窗口 | 会弹出黑色 cmd 窗口 | `utils/constants.CREATE_NO_WINDOW` 统一定义；`audio_io.suppress_windows_console_window` 给 ffmpeg / afplay / aplay 共用 |
| PTY `write()` 类型不一致 | 跨平台代码易崩溃 | `_env_base._pipe_stdin` 统一按 `isinstance(data, str)` 编码再走 `proc.stdin.buffer` |
| text-mode stdin `\n → \r\n` 转换 | 写入文件内容被破坏 | 统一使用 `proc.stdin.buffer`（二进制模式）写入 |
| 缺少 Windows 必需环境变量 | `socket` 抛 `WinError 10106` | `code_execution_tool` 提供 `_WINDOWS_ESSENTIAL_ENV_VARS` 必传子集 |

### 本地音频栈

| 问题 | 影响 | 缓解 |
|------|------|------|
| `piper-tts` 不在某些 Python 平台 wheels | 该平台 pip install 直接失败 | 检查 `platform.machine()` 后允许 pyttsx3 兜底或返回 `no_engine_installed` 友好错误 |
| `pyttsx3` 在 macOS 上 `import` 会触发 kTCCServiceMediaLibrary 弹窗 | 影响开发启动体验 | `_audio_capability` 在握手阶段检查 import，false 时 Client 隐藏语音 UI（不阻塞启动） |
| Whisper 模型首次下载需要出网（huggingface / hf-mirror） | 冷启动延迟 30–300 s | 模型下载到 `$DESKAGENT_HOME/models/whisper/` 后即永久离线；首次大调用前 Client 可预热 |
| ffmpeg 缺 PATH | `speech_to_text` 失败 | tool 返回 `"audio decode failed: ffmpeg binary not found"`；Client 据此引导用户装 ffmpeg |
| `faster-whisper` 接收 > 25 MB 音频 | 防止上下文爆炸（LLM 端 LLM context dump 攻击） | `audio_io.DEFAULT_MAX_INPUT_BYTES = 25 MiB` hard cap;调用方可在 `max_seconds` 二次限时长 |
