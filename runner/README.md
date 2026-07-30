# Runner

本地手脚——纯粹的工具执行器，承载伙伴"能帮用户做的事"。以 uv build wheel 形式发布，安装器在 `$DESKAGENT_HOME/runner/.venv` 创建 venv 并安装；Desktop 直接 spawn venv Python 调用 `server.py`，通过 WebSocket 接收 JSON-RPC 2.0 工具调用指令并在用户机器上执行。Runner 不感知"伙伴"语义——终端、文件、浏览器、代码执行等底层能力 100% 保留，伙伴人格完全由 Backend 承载、伙伴形象完全由 Desktop 渲染。

设计文档：[ARCHITECTURE.md](../ARCHITECTURE.md) §3 / §4 / §5 / §8

## 设计意图

- **剥离大脑逻辑**：系统提示词、多模型适配器、对话记忆模块全部由 Backend 承载。
- **剔除网络请求**：Runner 不保存任何用户 Token 或云端地址，无法直接访问 Backend。需借 LLM 时通过反向 RPC 请求 Desktop 代为调用（[ARCHITECTURE.md §5.2.II](../ARCHITECTURE.md)）。
- **Provider 范围**：产品 LLM 交互只面向 OpenAI-compatible providers，不接 Anthropic。Runner 不做 LLM provider 特定的 schema 适配（如折叠 `anyOf` null branch）——nullable union 原样传递，由目标 provider 决定能否接受。
- **环境状态与工具解耦**：环境共享态（活跃实例表、工厂、cleanup 线程）下沉到 `tools/terminal/environment/` 子包，`file_tools`、`code_execution_tool` 跨包直接导入该子包、共享同一批 env 实例，绕开仍含命令处理 / 安全审批逻辑的 `terminal_tool` 避免循环依赖。`terminal/__init__.py` 对 `terminal_tool` 的重导出用 `__getattr__` 惰性加载——包初始化时 terminal_tool → files → environment → `terminal/__init__` → terminal_tool 的环不会触发。

## 架构地图

```
runner/
├── server.py     # WebSocket JSON-RPC 入口（唯一入口）——runner_loop / get_tools / execute_tool / mcp.reload / request_llm
├── tools/        # 工具实现与自注册中心——terminal(6后端) / files / browser(多后端) / execute_code / process / skills / mcp / multimodal / toolsets / security / system(输出清洗·结果预算·凭据文件)
└── utils/        # 路径解析(DESKAGENT_HOME) / 配置 / 脱敏 / 文件安全 / PID 管理(Windows兼容) / 反向 RPC
```

Wheel 产物：`dist/deskagent-agent-*.whl`。Desktop spawn `$DESKAGENT_HOME/runner/.venv/{bin/python,Scripts/python.exe} $DESKAGENT_HOME/runner/server.py --desktop-ws <ws-url>`。安装布局详 [installer/README.md §9](../installer/README.md)。

## 通信协议

Runner 主动连接 Desktop 提供的本地 WS 服务器（`ws://127.0.0.1:<port>/rpc`），启动参数 `--desktop-ws`。连接后发送 `runner_ready` 握手。

**RPC 方法**（Desktop↔Runner 协议的完整方法集）：

| 方法 | 方向 | 用途 |
|------|------|------|
| `runner_ready` | Runner → Desktop | 启动握手通知 |
| `tools_changed` | Runner → Desktop | 工具 schema 变更通知（MCP 后台发现完成后触发）；Desktop 收到后重拉 `get_tools` 并重新 `tools.sync` 到 backend |
| `get_tools` | Desktop → Runner | 获取工具 Schema |
| `execute_tool` | Desktop → Runner | 执行工具调用 |
| `mcp.reload` | Desktop → Runner | 第一类 RPC（不走 `execute_tool`）：关闭当前所有 MCP 连接并从最新 `$DESKAGENT_HOME/config.yaml` 重新连接；同时清 `tool_output_limits` / `file_read_max_chars` 缓存，让相关 config 改动免重启生效。无入参（runner 始终读本地 YAML） |
| `deskagent.cancel` | Desktop → Runner | 中断信号：设 `_global_interrupt` 让 in-flight 工具下次轮询时退出 |
| `request_llm` | Runner → Desktop | 反向 RPC（带 `id` 的请求）：借用 LLM，响应体可含 `content` / `choices[0].message.content` / `text`，`server.py::_extract_llm_content` 做容错抽取 |

**自动退出与重连**：WS 断开后进入重连循环（指数退避 2s → 30s，最多 15 次约 5 分钟）。每次重试前读 `$DESKAGENT_HOME/desktop-endpoint.json` 获取最新端口（Desktop 重启后端口变化），并检查 Desktop PID 存活以跳过残留文件。超时后 `sys.exit(1)`。

## 工具系统

### 注册协议

每个工具模块在 import 时调用 `registry.register_tool(...)` 完成注册。`discover_builtin_tools()` 递归扫描 `tools/` 子包（跳过 `registry` 和 `mcp/mcp_tool`——MCP 模块的特性见下文）。

46 个静态注册工具覆盖终端（6 后端）、文件（read/write/patch/search/list）、浏览器（导航/交互/cookie/storage/tab 管理/CDP/dialog 等）、代码执行、进程管理、Skills（list/view/manage）、多模态（vision_analyze / computer_use）。完整工具名与 schema 见 `tools/` 各子包。

**Handler 契约**：接收 `**kwargs`，返回 JSON 字符串。

**Schema 要求**：每个工具必须提供显式 JSON Schema。Backend 依赖这些 schema 告知 LLM 工具参数。Runner 不做 provider-specific 适配。

**结果规范**：成功 `tool_result(...)`、失败 `tool_error(...)`，均返回 JSON 字符串。清洗流水线：`ansi_strip → strip_fence → redact`。大结果持久化到文件，返回 `<persisted-output>` 标签。

### MCP 动态工具发现

MCP 工具由 `discover_mcp_tools()` 在 `server_loop` 紧跟 `runner_ready` 之后**后台线程**调用，从 `$DESKAGENT_HOME/config.yaml` 的 `mcp_servers` 段读取配置、连接各 server、把 `mcp_<server>_<tool>` 注册到全局 registry。**这是 MCP 工具进入 backend LLM schema 的唯一入口**。

后台执行是为了让 bridge 握手在 <1s 完成——desktop 立刻收到 `runner_ready` 并 `get_tools` + `tools.sync` 静态工具；MCP 发现完成后 runner 发 `tools_changed`，desktop 重拉 + 重 `tools.sync`，LLM 在下一轮 turn 看到 MCP 工具。运行时新增/删除 MCP server 须经 Runner 重启（Desktop MCP 设置页 `runnerConfig.write` 走 `restartRunnerBridge`）才能让 backend 看到。

**MCP server 通知处理**：Runner 在 MCP `ClientSession` 上注册的 message handler 仅消费 `ToolListChangedNotification`——收到时重新拉取 tool 列表并热替换 registry 条目。`PromptListChangedNotification` 与 `ResourceListChangedNotification` 仅 debug 日志后被忽略（**设计意图而非 TODO**）：list-change 不触发热刷新，要看新增/删除的 prompt/resource 须经 Desktop 主动触发 `mcp.reload`，不能依赖 Runner 内的自动响应。

**Prompt / Resource 经 utility 工具暴露**：每个 MCP server 的 resources 和 prompts 注册为 `mcp_<server>_list_resources` / `_read_resource` / `_list_prompts` / `_get_prompt` 四个 utility 工具，与普通 MCP 工具一起进 LLM schema。双重门控：(1) server 在 initialize 握手声明了对应 capability（以 `initialize_result.capabilities` 为准，不靠 `hasattr(session, ...)`——`ClientSession` 总是定义这四个方法属性，旧判据从不过滤）；(2) config `mcp_servers.<name>.tools.resources` / `.prompts` 未显式关闭（默认开）。utility 工具同样走 `mcp_` 前缀、受同名 collision guard 保护。

**Schema 修复是 MCP 协议层而非 LLM 适配层**：`tools/mcp/mcp_tool.py` 内部的 `_rewrite_local_refs` / `_repair_object_shape` 是 MCP 协议本身需要的结构修复（处理 `$ref`、补 `type` 字段等），与目标 LLM provider 无关。

### 测试钉子

`test_startup_imports.py` 钉死 `server.py` 的每行 module-level import（MCP load-bearing 等关键传递依赖）。`.pre-commit-config.yaml` 在 runner 文件改动时跑它（<1s）；`build_client.{ps1,sh}` 在 `uv build --wheel` 之前跑整个 `tests/` 作为发布门——任意一层失败都拦下坏 wheel（env-rot、传递依赖损坏永远不应该出 repo）。

## 终端后端

执行后端在 `tools/terminal/_env_*.py`：`_env_base` 抽象类 + `_env_file_sync` 同步 helper + 4 个后端（`_env_local` PTY/Pipe、`_env_docker`、`_env_ssh` ControlMaster、`_env_singularity`/Apptainer）。进程级共享态与生命周期在 `environment/` 子包：`state.py` 持有 `_active_environments` / `_last_activity`，`factory.py` 按 `env_type` 分发，`cleanup.py` 跑后台 reaper。

**环境缓存与隔离**：env 实例按 `task_id` 缓存。`resolve_container_task_id` 把无镜像 override（`docker_image` / `singularity_image` / `env_type`）的 task 折叠到 `"default"`——local 默认下所有 task 共享同一 LocalEnvironment；带 override 的 task 拿到独立容器。docker/singularity 支持 persistent 容器（`container_persistent` 跨命令、`docker_persist_across_processes` 跨 runner 进程存活），reaper 按 `lifetime_seconds` 回收孤儿容器。

**命令执行模型**：spawn-per-call + 会话快照——每条命令起新 bash，先 source 快照恢复 exports/functions/aliases，执行后写回；CWD 用 marker-based stdout 提取。并行 terminal 不安全且未被锁保护：同一 env 实例的快照/CWD 文件不可并发写，默认 local 共享实例意味着并发命令互相覆盖——这是真约束，调用方须自行串行化。

## 安全机制

### 危险命令审批

关键不变量：**Hardline blocklist 永远生效，YOLO 模式不可绕**——`force=True`（YOLO）仅跳过非硬阻断项。`SUDO_PASSWORD` 缺失时拒绝 `sudo -S`（防密码猜测）。

### 路径安全（`utils/file_safety.py`）

写拒绝列表：SSH 密钥、AWS/GCP/Kube 凭据、OAuth token、shell 配置、/etc/sudoers、/etc/passwd、/etc/shadow。

**Windows 路径大小写不敏感**：`is_write_denied` 对**两侧**路径都做 `replace("\\", "/").lower()` 归一化再比对——`C:\Windows\System32` / `c:/windows/system32` / `C:/WINDOWS\System32\` 都命中同一前缀。POSIX 路径**不**做大小写折叠。`get_windows_sensitive_prefixes()` 经 `winapi.GetLogicalDrives` 枚举**所有**挂载盘符（不止 `C:`），所以 `D:\Windows\...`（Windows 装在 D: 的企业镜像）或网络共享里嵌的 `Windows` 目录树同样被拦截。

### 跨 profile / 跨沙箱镜像写检测

三种 cross-scope 写检测走同一条 soft-guard 路径：

| 检测器 | 命中的路径形状 | 默认行为 |
|--------|----------------|----------|
| `cross-profile` | 另一 profile 的 `skills/plugins/cron/memories` 目录 | 软警告，agent 可经 `cross_profile=True` opt-in（需用户明确许可） |
| `sandbox-mirror` | host-side `…/sandboxes/<backend>/<task>/home/.deskagent/…`（Docker / Daytona 等非本地后端的绑定镜像） | 软警告 |
| `container-mirror` | Docker 容器内部去前缀后的 `/root/.deskagent/…` 路径 | 软警告 |

设计意图：**soft-guard 而非 hard block**——同一 OS 用户下，agent 通过 terminal 工具本身就能写到任何路径，所以硬阻断只会给"虚假的安心感"。soft-guard 让 agent 在 LLM 提示层看到警告，必须先获得用户 `cross_profile=True` 才能覆盖。三个检测器共享同一道 opt-in 闸门，不是每个 detector 各自一份 override。

### URL 安全（`tools/browser/url_safety.py`）

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

**平台过滤**：`skills_tool.py::skill_matches_platform` 将 SKILL.md frontmatter 的 `platforms`（`macos` / `windows` / `linux`）通过 `_PLATFORM_MAP` 翻译成 `sys.platform` 字符串（`darwin` / `win32` / `linux`）再比较。两套字符串不能直接对比——必须走 `_PLATFORM_MAP` 翻译。Desktop `lib/skill-index.cjs` 在 main 进程同样做翻译；两套表结构不同但语义对齐，新增平台时需同步更新两边。

## 工具集系统（Toolsets）

Toolsets 是用户可平移的 LLM-facing schema 过滤单位。Catalog 是**三个镜像**的真相（Desktop TS / Desktop CJS / Runner Python），同提交同步。

启用/禁用配置在 `$DESKAGENT_HOME/config.yaml::toolsets.disabled`（Desktop IPC 写、Runner `toolsets/helpers.py::get_disabled_toolset_ids` 读）。过滤在 `registry.get_schemas_for_llm(set_of_disabled_ids)` 生效——被禁工具集对应 catalog 中的 `prefixes` 项扫实际注册表的 tool 名命中即排除；`extra_tools` 按名命中即排除；**MCP 工具（`mcp_*`）无条件排除**（它们的 toggle 在 MCP settings 页）。

**与 backend CORE_TOOLS 的分层语义**：`backend/core/chat_service.py::CORE_TOOLS` 是 Backend 侧的**硬保证**白名单；工具集过滤在更上游（Runner `get_tools` RPC），是**软补充**。两层语义独立。

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
| `subprocess.Popen` 在 Windows 需隐藏控制台窗口 | 会弹出黑色 cmd 窗口 | `utils/constants.CREATE_NO_WINDOW` 统一定义 |
| PTY `write()` 类型不一致 | 跨平台代码易崩溃 | `_env_base._pipe_stdin` 统一按 `isinstance(data, str)` 编码再走 `proc.stdin.buffer` |
| text-mode stdin `\n → \r\n` 转换 | 写入文件内容被破坏 | 统一使用 `proc.stdin.buffer`（二进制模式）写入 |
| 缺少 Windows 必需环境变量 | `socket` 抛 `WinError 10106` | `code_execution_tool` 提供 `_WINDOWS_ESSENTIAL_ENV_VARS` 必传子集 |
