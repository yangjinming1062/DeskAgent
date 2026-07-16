# Runner

本地手脚——纯粹的工具执行器。以 uv build wheel 形式发布，安装器在 `$ZAST_HOME/runner/.venv` 创建 venv 并安装；Desktop 直接 spawn venv Python 调用 `server.py`，通过 WebSocket 接收 JSON-RPC 2.0 工具调用指令并在用户机器上执行。

设计文档：[design.md](../design.md) §2.3 / §3.2 / §5.5

## 设计意图

- **剥离大脑逻辑**：系统提示词、多模型适配器、对话记忆模块全部由 Backend 承载
- **剔除网络请求**：Runner 不保存任何用户 Token 或云端地址，无法直接访问 Backend
- **保留底层能力**：终端、文件、浏览器、代码执行等与操作系统直接交互的工具 100% 保留
- **Token 隔离**：Runner 不持有 Backend Token，需借 LLM 时通过反向 RPC 请求 Desktop 代为调用
- **Provider 范围**：产品 LLM 交互只计划使用 OpenAI SDK，模型不接 Anthropic。Runner 不做 LLM provider 特定的 schema 适配（如折叠 `anyOf` null branch）——nullable union 原样传递，由目标 provider 决定能否接受
- **环境状态与工具解耦**：环境生命周期管理（`_active_environments`、工厂、清理线程等）提取到 `tools/terminal/environment.py`，`terminal_tool.py` 只保留终端命令执行的 handler 和 schema。`file_tools`、`code_execution_tool`、`process_tool` 从 `environment.py` 直接导入，无循环依赖。`terminal/__init__.py` 对 `terminal_tool` 的重导出使用 `__getattr__` 惰性加载，避免包初始化时触发循环

## 目录结构

```
runner/
├── server.py            # WebSocket JSON-RPC 入口（唯一入口）
├── tools/               # 工具实现与注册
│   ├── registry.py          # 自注册中心（ToolRegistry 单例）
│   ├── interrupt.py         # 中断信号管理
│   ├── thread_context.py    # 线程上下文传播
│   ├── tool_output_limits.py    # 工具输出限制（默认 50KB/2000行）
│   ├── tool_result_storage.py   # 大结果持久化
│   ├── debug_helpers.py     # 调试会话日志
│   ├── terminal/            # 终端工具（6 后端模块:base + file_sync + 4 执行后端）
│   │   ├── terminal_tool.py
│   │   ├── environment.py   # 环境生命周期管理（状态、工厂、查询、清理）
│   │   ├── _env_base.py     # BaseEnvironment 抽象类
│   │   ├── _env_local.py    # 本地进程（PTY/Pipe）
│   │   ├── _env_docker.py   # Docker 容器
│   │   ├── _env_singularity.py  # Singularity/Apptainer
│   │   ├── _env_ssh.py      # SSH（ControlMaster）
│   │   └── _env_file_sync.py    # 容器/远程与本地的文件双向同步
│   ├── browser/             # 浏览器工具（多后端）
│   │   ├── browser_tool.py      # 主工具（10 个注册工具）
│   │   ├── browser_cdp_tool.py  # CDP passthrough
│   │   ├── browser_dialog_tool.py   # JS dialog 响应
│   │   ├── browser_supervisor.py    # CDP Supervisor
│   │   ├── browser_camofox.py       # Camofox 反检测
│   │   ├── browser_camofox_state.py
│   │   ├── helpers.py
│   │   ├── url_safety.py    # SSRF 防护
│   │   └── website_policy.py
│   ├── files/               # 文件工具
│   │   ├── file_tools.py        # read_file / write_file / patch / search_files
│   │   ├── helpers.py           # 文件操作/状态/补丁辅助
│   │   ├── path_security.py
│   │   ├── fuzzy_match.py       # 模糊匹配
│   │   └── binary_extensions.py
│   ├── execute_code/        # 代码执行工具
│   │   └── code_execution_tool.py
│   ├── process/             # 后台进程管理
│   │   └── process_tool.py
│   ├── skills/              # Skills 系统
│   │   ├── skills_tool.py       # skills_list / skill_view
│   │   ├── skill_manager_tool.py
│   │   ├── helpers.py
│   │   ├── skill_provenance.py
│   │   ├── skill_usage.py
│   │   ├── skills_guard.py
│   │   ├── skills_hub.py
│   │   └── skills_sync.py
│   ├── mcp/                 # MCP 协议工具
│   │   ├── mcp_tool.py      # 3550 行，最大文件
│   │   ├── helpers.py
│   │   └── osv_check.py     # OSV 恶意包检查
│   ├── multimodal/          # 多模态工具
│   │   ├── vision_tool.py       # vision_analyze
│   │   ├── video_tool.py        # video_analyze
│   │   ├── computer_use_tool.py # computer_use（桌面操作）
│   │   ├── cu_schema.py         # computer-use 操作 schema
│   │   ├── cu_tool.py           # computer-use tool handler
│   │   ├── cu_backend.py        # computer-use 后端基类
│   │   ├── cu_cua_backend.py    # CUA 后端
│   │   ├── cu_win_backend.py    # Windows 后端
│   │   ├── cu_permissions.py    # 操作权限审批
│   │   └── helpers.py
│   ├── system/              # 共享系统模块
│   │   ├── ansi_strip.py
│   │   ├── budget_config.py
│   │   ├── clean.py
│   │   ├── credential_files.py
│   │   ├── env_passthrough.py
│   │   └── environments/
│   ├── security/            # 安全模块
│   │   └── tirith_security.py   # tirith 安全扫描
│   └── toolsets/            # 工具集过滤模块
│       ├── catalog.py       # 工具集目录
│       └── helpers.py       # 工具集助手
└── utils/               # 环境 helper
    ├── __init__.py
    ├── constants.py     # 路径解析（ZAST_HOME 等）
    ├── config.py        # 配置加载
    ├── redact.py        # 结果脱敏（API key/JWT/连接字符串）
    ├── file_safety.py   # 文件安全检查（写拒绝列表）
    ├── file_io.py
    ├── path_helpers.py
    ├── pid.py           # pid_exists / kill_tree（Windows 兼容）
    ├── env_helpers.py   # 环境变量清洗（sanitize_subprocess_env）
    ├── reverse_rpc.py   # call_llm → request_llm_from_desktop
    └── async_bridge.py
```

`tests/`（pytest）：
- `test_tools.py` 覆盖 registry 加载、Schema 完整性、`tool_result` / `tool_error` helpers（断言工具总数 ≥ 20 且每个 schema 含 `name` + `parameters`）
- `test_computer_use.py` 覆盖 computer-use 工具
- `test_path_helpers.py` 覆盖 path 解析 helpers
- `test_startup_imports.py` 钉死 `server.py:9-22` 的每行 module-level import(MCP load-bearing 等关键传递依赖)。`.pre-commit-config.yaml` 在 runner 文件改动时跑它(`<1s`);`build_client.{ps1,sh}` 在 `uv build --wheel` 之前跑整个 `tests/` 作为发布门 — 任意一个层失败都拦下坏 wheel(env-rot、传递依赖损坏永远不应该出 repo)

Wheel 产物：`runner/dist/zast_agent-*.whl`。安装器在 stage 3 创建 `$ZAST_HOME/runner/.venv` 并 `uv pip install` 这个 wheel；Desktop spawn `$ZAST_HOME/runner/.venv/{bin/python,Scripts/python.exe}` 调用 `$ZAST_HOME/runner/server.py`。

## 通信协议

### WebSocket JSON-RPC 2.0

Runner 主动连接 Desktop 提供的本地 WS 服务器（`ws://127.0.0.1:<port>/rpc`）。

**启动参数**：`--desktop-ws ws://127.0.0.1:<port>/rpc`

**启动握手**：连接后发送 `{"jsonrpc":"2.0", "method":"runner_ready", "params":{}}`

**工具调用**（Desktop → Runner）：
```json
{"jsonrpc":"2.0", "id":"call_abc", "method":"execute_tool",
 "params":{"name":"terminal", "args":{"command":"pwd"}}}
```

**返回结果**（Runner → Desktop）：
```json
{"jsonrpc":"2.0", "id":"call_abc",
 "result":{"stdout":"/home/user", "exit_code":0}}
```

**RPC 方法**：
| 方法 | 方向 | 用途 |
|------|------|------|
| `runner_ready` | Runner → Desktop | 启动握手通知 |
| `tools_changed` | Runner → Desktop | 工具 schema 变更通知（启动后 MCP 后台发现完成触发）；Desktop 收到后重拉 `get_tools` 并重新 `tools.sync` 到 backend |
| `get_tools` | Desktop → Runner | 获取工具 Schema |
| `execute_tool` | Desktop → Runner | 执行工具调用 |
| `mcp.reload` | Desktop → Runner | 第一类 RPC（不走 `execute_tool`）：关闭当前所有 MCP 连接并从最新 `$ZAST_HOME/config.yaml` 重新连接，回复 `{reloaded, errors, servers, connected}`。无入参（runner 始终读本地 YAML） |
| `zast.cancel` | Desktop → Runner | 中断信号：设 `_global_interrupt` 让 in-flight 工具下次轮询时退出；返回 `{ok: true}` |
| `request_llm` | Runner → Desktop | 反向 RPC（带 `id` 的请求）：借用 LLM，响应体可含 `content` / `choices[0].message.content` / `text`，`server.py::_extract_llm_content` 做容错抽取 |

### 反向 RPC

Runner 需要借用 LLM 时（如 vision_analyze、browser 长文本检索、MCP sampling），通过 `utils/reverse_rpc.py` 的 `call_llm()` 发送 `request_llm` 通知给 Desktop，Desktop 转调 Backend `POST /api/llm/completion`。

### 自动退出与重连

WebSocket 断开后 Runner 进入重连循环（指数退避 2s → 30s，最多 15 次约 5 分钟）。每次重试前读取 `$ZAST_HOME/desktop-endpoint.json` 获取最新端口（Desktop 重启后端口变化），并检查 Desktop PID 存活以跳过残留文件。超时后 `sys.exit(1)`。

## 工具系统

### 注册协议

每个工具模块在 import 时调用 `registry.register_tool(...)` 完成注册。`discover_builtin_tools()` 递归扫描 `tools/` 子包（跳过 `registry` 和 `mcp/mcp_tool`——MCP 模块的特殊性见下文）。

MCP 工具由 `discover_mcp_tools()` 在 `server.py::runner_loop` 紧跟 `runner_ready` 之后**后台线程**调用（`_schedule_background_mcp_discovery`），从 `$ZAST_HOME/config.yaml` 的 `mcp_servers` 段读取配置、连接各 server、把 `mcp_<server>_<tool>` 注册到全局 registry。**这是 MCP 工具进入 backend LLM schema 的唯一入口**。后台执行是为了让 bridge 握手在 <1s 完成——desktop 立刻收到 `runner_ready` 并 `get_tools` + `tools.sync` 25 个静态工具；MCP 发现完成后 runner 发 `tools_changed` 通知，desktop 重拉 + 重 `tools.sync`，LLM 在下一轮 turn 看到 MCP 工具。运行时新增/删除 MCP server 须经 Runner 重启（Desktop MCP 设置页 `runnerConfig.write` 走 `restartRunnerBridge`）才能让 backend 看到。

**Handler 契约**：接收 `**kwargs`，返回 JSON 字符串。

**Schema 要求**：每个工具必须提供显式 JSON Schema。Backend 依赖这些 schema 告知 LLM 工具参数。Runner 不做 provider-specific 适配——产品只面向 OpenAI-compatible providers，nullable `anyOf` 原样传递。

### 已注册工具

47 个静态注册工具 + N 个 MCP 动态工具（启动时由 `discover_mcp_tools()` 从 `$ZAST_HOME/config.yaml` 读取；N 取决于用户配置的 MCP server 数）：

| 工具名 | 来源文件 | 说明 |
|--------|----------|------|
| `terminal` | terminal/terminal_tool.py | 终端命令执行(6 个 _env_*.py 模块:base + file_sync + 4 执行后端) |
| `list_directory` | files/file_tools.py | 列出目录内容（文件名、大小、修改时间） |
| `read_file` | files/file_tools.py | 读文件 |
| `write_file` | files/file_tools.py | 写文件 |
| `patch` | files/file_tools.py | 补丁应用 |
| `search_files` | files/file_tools.py | 文件搜索 |
| `execute_code` | execute_code/code_execution_tool.py | 沙箱 Python 执行 |
| `process` | process/process_tool.py | 后台进程管理 |
| `browser_navigate` | browser/browser_tool.py | 浏览器导航 |
| `browser_snapshot` | browser/browser_tool.py | 浏览器快照 |
| `browser_click` | browser/browser_tool.py | 浏览器点击 |
| `browser_type` | browser/browser_tool.py | 浏览器输入 |
| `browser_scroll` | browser/browser_tool.py | 浏览器滚动 |
| `browser_back` | browser/browser_tool.py | 浏览器后退 |
| `browser_press` | browser/browser_tool.py | 浏览器按键 |
| `browser_get_images` | browser/browser_tool.py | 获取页面图片 |
| `browser_vision` | browser/browser_tool.py | 浏览器视觉分析 |
| `browser_console` | browser/browser_tool.py | 浏览器控制台 |
| `browser_hover` | browser/browser_tool.py | 浏览器悬停（触发 :hover / 下拉菜单）|
| `browser_wait_for` | browser/browser_tool.py | 等待 selector / text 出现（轮询 live DOM）|
| `browser_find` | browser/browser_tool.py | 在 live DOM 中按文本查找元素并返回 ref |
| `browser_drag` | browser/browser_tool.py | 拖拽元素（CDP Input.dispatchMouseEvent 序列）|
| `browser_select` | browser/browser_tool.py | 选择下拉选项（原生 `<select>` 或常见框架自定义下拉）|
| `browser_download` | browser/browser_tool.py | 下载文件（点击链接或 URL，阻塞等待完成）|
| `browser_pdf` | browser/browser_tool.py | 将当前页面保存为 PDF |
| `browser_screenshot_element` | browser/browser_tool.py | 按 ref 截取单个元素的截图 |
| `browser_cookies_get` | browser/browser_cookie_tool.py | 读取 cookie（CDP `Network.getCookies`）|
| `browser_cookies_set` | browser/browser_cookie_tool.py | 设置 cookie（CDP `Network.setCookie`）|
| `browser_cookies_clear` | browser/browser_cookie_tool.py | 清空 cookie / 存储（CDP `Network.clearBrowserCookies` + `Storage.clearDataOrigin`）|
| `browser_storage_get` | browser/browser_cookie_tool.py | 读取 localStorage / sessionStorage |
| `browser_storage_set` | browser/browser_cookie_tool.py | 写入 localStorage / sessionStorage |
| `browser_tab_new` | browser/browser_tool.py | 新建浏览器 tab 并切换激活（仅本地后端）|
| `browser_tab_switch` | browser/browser_tool.py | 切换到指定 tab（同时切换 CDPSupervisor active_session_id）|
| `browser_tab_close` | browser/browser_tool.py | 关闭 tab（缺省关激活的 tab）|
| `browser_tab_list` | browser/browser_tool.py | 列出所有打开的 tab |
| `browser_set_viewport` | browser/browser_tool.py | 覆盖视口大小（CDP Emulation）|
| `browser_set_user_agent` | browser/browser_tool.py | 覆盖 UA 字符串（CDP Network）|
| `browser_set_extra_headers` | browser/browser_tool.py | 替换所有额外 HTTP 头（CDP Network，wholesale）|
| `browser_set_geolocation` | browser/browser_tool.py | 覆盖浏览器地理位置（CDP Emulation）|

持久化 profile（cookie + storage 跨重启存活）：[profile_manager.py](runner/tools/browser/profile_manager.py) 解析 `$ZAST_HOME/browser_profiles/<name>` 路径，检查 `SingletonLock`/`LOCK` 锁冲突，并在后台 cleanup 线程每 30s 跑一次 72h 保留 GC。`_create_local_session` 把 `profile_dir` 注入到 `--user-data-dir` agent-browser 命令行参数。
| `browser_cdp` | browser/browser_cdp_tool.py | CDP passthrough |
| `browser_dialog` | browser/browser_dialog_tool.py | JS dialog 响应 |
| `skills_list` | skills/skills_tool.py | 列出 Skills |
| `skill_view` | skills/skills_tool.py | 查看 Skill 内容 |
| `skill_manage` | skills/skill_manager_tool.py | 创建/修改 Skills |
| `vision_analyze` | multimodal/vision_tool.py | 图片分析 |
| `video_analyze` | multimodal/video_tool.py | 视频分析 |
| `computer_use` | multimodal/computer_use_tool.py | 桌面操作（computer-use） |
| `mcp_<server>_<tool>` | mcp/mcp_tool.py | 动态 MCP 工具 |

### 结果规范

- 成功：`tool_result(...)` 返回 JSON 字符串
- 失败：`tool_error(...)` 返回错误信息
- 清洗流水线：`ansi_strip → strip_fence → redact`
- 大结果持久化到文件，返回 `<persisted-output>` 标签

### MCP server 通知处理（`tools/mcp/mcp_tool.py::_make_message_handler`）

Runner 在 MCP `ClientSession` 上注册的 message handler 仅消费 `ToolListChangedNotification`——收到时调度一次 `_refresh_tools()` 重新拉取 tool 列表并热替换 `ToolRegistry` 条目。`PromptListChangedNotification` 与 `ResourceListChangedNotification` 是 debug 日志后被忽略（**这是设计意图而非 TODO**）：runner 当前的工具 surface 只覆盖 tools，prompts/resources 暂未通过任何 Zast 端工具暴露，所以即便 server 端列表变化也不会引起 stale registry。需要刷新 prompt/resource 的调用方必须经 Desktop 主动触发 `reload.mcp`（`backend/routers/chat.py::reload_mcp` → `core/ipc.dispatch_user_event` → desktop `zast:runner:dispatch` → runner `mcp.reload`），不能依赖 Runner 内的自动响应。

## 终端后端

`tools/terminal/_env_*.py` 实现 6 个模块:`base` 抽象类 + `file_sync` 同步 helper + 4 个执行后端:

| 模块 | 说明 | 安全加固 |
|------|------|----------|
| **base** | (`_env_base.py`)所有后端的抽象基类 | — |
| **file_sync** | (`_env_file_sync.py`)容器/远程与本地之间的文件双向同步 | — |
| **local** | 本地进程（PTY/Pipe） | 进程组管理 |
| **docker** | Docker 容器 | cap-drop ALL、no-new-privileges、pids-limit 256、tmpfs |
| **ssh** | SSH（ControlMaster） | 文件同步（credentials/skills） |
| **singularity** | Singularity/Apptainer | overlay 持久化 |

采用 **spawn-per-call + 会话快照** 模型：exports/aliases/functions 在快照文件中捕获，每次命令前 source。CWD 跟踪使用 marker-based stdout 提取。

## 安全机制

### 危险命令审批

审批逻辑分散在以下位置,均在工具执行路径内:

- `tools/terminal/terminal_tool.py` — `check_command_security()`（tirith 扫描）在命令执行前拦截危险命令，`force=True`（YOLO）仅跳过非硬阻断项
- `tools/security/tirith_security.py` — hardline blocklist 与 tirith 审批实现
- `tools/files/file_tools.py` — "exec approval 可被 prompt injection 关闭"的旁路防护（[第 449-451 行](tools/files/file_tools.py) 注释解释）；写拒绝列表实现在 [utils/file_safety.py](utils/file_safety.py) (`is_write_denied`)，不在 file_tools.py 本身
- `tools/multimodal/cu_schema.py` — computer-use 操作的审批语义([第 37 行](tools/multimodal/cu_schema.py))
- `tools/thread_context.py` — 父进程审批 / sudo 回调捕获([第 17 行](tools/thread_context.py))

关键不变量:**Hardline blocklist 永远生效,YOLO 模式不可绕**;`SUDO_PASSWORD` 缺失时拒绝 `sudo -S`(防密码猜测)。

### Schema 修复（MCP 协议层，非 LLM 适配层）

Runner 侧不存在 LLM provider 特定的 schema 适配。`tools/mcp/mcp_tool.py` 内部的 `_rewrite_local_refs` / `_repair_object_shape` 是 MCP 协议本身需要的结构修复（处理 `$ref`、补 `type` 字段等），与目标 LLM provider 无关——OpenAI 工具 schema 原样接受 MCP 输出的 `anyOf` nullable union。
### 路径安全（utils/file_safety.py）

写拒绝列表：SSH 密钥、AWS/GCP/Kube 凭据、OAuth token、shell 配置、/etc/sudoers、/etc/passwd、/etc/shadow。跨 profile 写防护。

**Windows 路径大小写不敏感**：`is_write_denied` 与 `_build_normalized_prefixes` 在 Windows 上对**两侧**路径都做 `replace("\\", "/").lower()` 归一化再比对——`C:\Windows\System32` / `c:/windows/system32` / `C:/WINDOWS/System32\` 都会命中同一前缀。POSIX 路径**不**做大小写折叠（macOS 默认 HFS+/APFS 也不大小写敏感，但工具保持显式不做，因为 docker 容器里通常跑 ext4，且 Linux 上 `readlink` 行为不一致）。`get_windows_sensitive_prefixes()` 通过 `winapi.GetLogicalDrives` 枚举**所有**挂载盘符（不止 `C:`），所以 `D:\Windows\...`（Windows 装在 D: 的企业镜像）或网络共享里嵌的 `Windows` 目录树同样被拦截。

### 跨 profile / 跨沙箱镜像写检测（`tools/files/file_tools.py::_check_cross_profile_path`）

三种 cross-scope 写检测走同一条 soft-guard 路径（`#32049` 系列引入，2026-06 拍板"保持 soft-guard，不做 hard block"）：

| 检测器 | 命中的路径形状 | 默认行为 |
|--------|----------------|----------|
| `cross-profile` | 另一 profile 的 `skills/plugins/cron/memories` 目录 | 软警告，agent 可经 `cross_profile=True` opt-in（需用户明确许可） |
| `sandbox-mirror` | host-side `…/sandboxes/<backend>/<task>/home/.zast/…`（Docker / Daytona 等非本地后端的绑定镜像） | 软警告 |
| `container-mirror` | Docker 容器内部去前缀后的 `/root/.zast/…` 路径 | 软警告 |

设计意图：**soft-guard 而非 hard block**——同一 OS 用户下，agent 通过 terminal 工具本身就能写到任何路径，所以硬阻断只会给"虚假的安心感"。soft-guard 让 agent 在 LLM 提示层看到警告，必须先获得用户 `cross_profile=True` 才能覆盖。三个检测器共享同一道 opt-in 闸门（位于 write 工具的 `cross_profile` 入参），不是每个 detector 各自一份 override。

### URL 安全（tools/browser/url_safety.py）

SSRF 防护：block private IPs、loopback、link-local、CGNAT（100.64.0.0/10）、云 metadata（169.254.169.254、metadata.google.internal）。DNS 解析校验。

### Tirith 安全扫描（tools/security/tirith_security.py）

自动安装 tirith 二进制（cosign 验证 + SHA-256）。每次终端命令执行 `tirith check --json --non-interactive --shell posix`。Fail-open（`tirith_fail_open` config flag，默认 True）。

### Secret 脱敏（utils/redact.py）

正则匹配 API keys（sk-*、ghp_*、AKIA* 等）、JWT、连接字符串、私钥。

### execute_code 沙箱

环境变量清洗（仅 PATH/HOME/USER/LANG/LC_*/TERM/TMPDIR/SHELL/XDG_*/ZAST_* 通过）。工具调用限制 50 次/脚本。5 分钟超时。50KB stdout 上限。

### MCP OSV 检查（tools/mcp/osv_check.py）

启动 stdio MCP server 前查询 OSV API 的 MAL-* advisory。

## Skills 系统

Skills 由安装器 seed 到 `$ZAST_HOME/skills/`:

| 层 | 路径 | 谁写 | 谁读 |
|----|------|------|------|
| Bundled source | `<repo>/installer/skills/` | 仓库维护者 | 安装脚本 |
| 运行时位置 | `$ZAST_HOME/skills/` | 安装脚本 | Runner |
| 启用/禁用配置 | `$ZAST_HOME/config.yaml::skills.disabled` | Desktop(`zast:skill:set-enabled` IPC) | Runner(`get_disabled_skill_names`);category-grain 匹配,单条 entry 覆盖整个 category 下的所有 leaf |

**唯一的路径 knob**：`ZAST_HOME`（默认 `$HOME/.zast` 或 `%LOCALAPPDATA%\zast`）。

**Skills 路径单一来源**：所有 skills 模块都通过 `utils.get_skills_dir()` 解析运行时 skills 路径，不允许再各自 `ZAST_HOME / "skills"` 或本地 `_skills_dir()` 包装函数。新增 skill 模块前先 `from utils import get_skills_dir`。

**平台过滤**：`tools/skills/skills_tool.py::skill_matches_platform` 将 SKILL.md frontmatter 的 `platforms`（人话：`macos` / `windows` / `linux`）通过模块顶部 `_PLATFORM_MAP` 翻译成 `sys.platform` 字符串（`darwin` / `win32` / `linux`），再做 `sys.platform in [...]` 比较。两套字符串不能直接对比 —— 必须走 `_PLATFORM_MAP` 翻译，否则 `darwin in ['macos']` 永真为 False。Desktop `lib/skill-index.cjs` 在 main 进程同样做翻译，给 renderer 发 `compatible: bool` 用来隐藏不兼容的行；两套表（Node 的 alias→canonical 与 Python 的 canonical→sys.platform）结构不同但语义对齐，新增平台时需同步更新两边。

## 工具集系统（Toolsets）

Toolsets 是用户可平移的 LLM-facing schema 过滤单位。Catalog 是**三个镜像**的真相（Desktop TS / Desktop CJS / Runner Python，详见各文件顶部 DocBlock），同提交同步。

| 层 | 路径 | 谁写 | 谁读 |
|----|------|------|------|
| 启用/禁用配置 | `$ZAST_HOME/config.yaml::toolsets.disabled` | Desktop(`zast:toolset:set-enabled` IPC) | Runner(`toolsets/helpers.py::get_disabled_toolset_ids`) |
| Catalog | `runner/tools/toolsets/catalog.py::TOOLSET_CATALOG` + 两侧镜像 | 仓库维护者 | Runner filter + Desktop UI |
| 过滤生效点 | `registry.get_schemas_for_llm(set_of_disabled_ids)` | — | `server.py` `get_tools` RPC handler 调 |

**过滤逻辑** (`toolsets/catalog.py::excluded_tool_names`)：被禁工具集对应 catalog 中的 `prefixes` 项扫实际注册表的 tool 名（`get_all_tool_names()`），命中即排除；`extra_tools` 项按名命中即排除；**MCP 工具（`mcp_*`）无条件排除**（它们的 toggle 在 MCP settings 页，不在这里）。

**与 CORE_TOOLS 的分层语义**：`backend/core/chat_service.py::CORE_TOOLS` 是 Backend 侧的**硬保证**白名单；工具集过滤在更上游（Runner `get_tools` RPC），是**软补充**。两层语义独立：禁用的工具不在 Runner 返回的 schema 列表里出现；CORE_TOOLS 不影响过滤结果。`tools.sync` 上报的即 Runner `get_schemas_for_llm` 的过滤结果。

**重启语义**：Desktop `zast:toolset:set-enabled` 走 atomic write + `restartRunnerBridge()`，确保 `disabled_toolsets` 集合在 Runner 进程重建时是最新的（Registry 是一次性 init，没有热重载入口，进程级 restart 是唯一路径）。

## 浏览器多后端

| 后端 | 说明 |
|------|------|
| **Local** | `agent-browser` CLI（headless Chromium），支持 Lightpanda |
| **Camofox** | 远程反检测浏览器（REST API） |
| **CDP passthrough** | 直连 Chrome DevTools Protocol |
| **Hybrid** | 私有/LAN URL 自动路由到本地 Chromium |

CDP Supervisor（`browser_supervisor.py`）：持久 WebSocket 到 CDP，dialog 拦截（Fetch domain 注入 JS bridge）、frame tree 跟踪、console 监控。

### 后端选择范围

支持的后端仅三种：Local headless Chromium（`agent-browser` CLI）、Camofox 远程反检测浏览器、用户自供的 CDP endpoint。不支持 Browserbase / Browser-Use v3 / Firecrawl 这类云浏览器 provider。若未来要恢复云浏览器能力，需要同时引入 provider 注册 + 凭证管理 + `browser_*` 工具 routing 逻辑。

## 已知限制

### 平台陷阱（Windows）

| 问题 | 影响 | 当前缓解 |
|------|------|----------|
| `os.kill(pid, 0)` 在 Windows 实际终止进程 | 存活检查会误杀进程 | 统一走 `utils/pid.pid_exists()`，Windows 上经 `psutil.pid_exists()` 避免 |
| `psutil.children(recursive=True)` PPID 链在 Windows 易过期 | 子进程树可能漏杀 | `utils/pid.kill_tree()` 统一在 Windows 上发起 `taskkill /T [/F]` |
| `subprocess.Popen` 在 Windows 需隐藏控制台窗口 | 会弹出黑色 cmd 窗口 | `utils/constants.CREATE_NO_WINDOW` 统一定义，各处引用 |
| PTY `write()` 类型不一致 | 跨平台代码易崩溃 | `_env_base._pipe_stdin` 统一按 `isinstance(data, str)` 编码再走 `proc.stdin.buffer`|
| text-mode stdin `\n → \r\n` 转换 | 写入文件内容被破坏 | 统一使用 `proc.stdin.buffer` (二进制模式) 写入 |
| 缺少 Windows 必需环境变量 | `socket` 抛 `WinError 10106` | `code_execution_tool` 提供 `_WINDOWS_ESSENTIAL_ENV_VARS` 必传子集 |
