# ZAST Agent System Architecture

> **System overview for humans and agents. Read before reading code.**

本系统是一个“云端思考、本地执行”的解耦型智能体（LLM Agent）系统。它源自 [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) 的二次开发，通过将“大脑决策”与“物理执行”在物理与逻辑层面进行彻底解耦，解决了传统 Agent 无法云端化管控、凭证易泄露、以及迭代效率低等痛点。

---

## 1. 架构拓扑 (Topology)

系统在物理上被拆分为三个核心子模块以及一个独立的安装器引导组件：

```
┌─────────────────────────────────────────────────────────────┐
│                        Backend                              │
│                   (云端大脑 / FastAPI)                        │
│  - 多用户会话、长短期记忆管理                                 │
│  - LLM 编排与系统提示词（System Prompt）装配                 │
│  - 云端工具直接执行（Web 搜索、TTS、图片生成）                │
│  - 事件发布 / Cron 调度中转（Outbox）                        │
└─────────────────────────┬───────────────────────────────────┘
                          │ WebSocket 长连接 (带用户 JWT 鉴权)
                          │ /api/chat/ws?token=<jwt>
┌─────────────────────────▼───────────────────────────────────┐
│                       Desktop                               │
│                (桌面枢纽 / Electron)                          │
│  - 登录鉴权管理与用户凭证加密落盘 (safeStorage)             │
│  - 本地 WebSocket 服务端与 Runner 进程生命周期生命管理      │
│  - 双向工具调用路由及反向 RPC (Reverse RPC) 代理中转         │
│  - 主聊天窗口 (React) 与录屏透明工具栏双窗口架构              │
└─────────────────────────┬───────────────────────────────────┘
                          │ 本地 WebSocket (动态高位端口)
                          │ ws://127.0.0.1:<port>/rpc
┌─────────────────────────▼───────────────────────────────────┐
│                        Runner                               │
│            (本地手脚 / uv build wheel)                       │
│  - 本地环境执行器（PTY 终端、文件读写、代码沙箱）            │
│  - 动态 MCP 协议客户端（加载本地 config.yaml）              │
│  - 浏览器多后端控制（Playwright / Camofox）                 │
│  - 自动化安全审查拦截器（Tirith 扫描 / 写白名单防护）        │
└─────────────────────────────────────────────────────────────┘
```

与此并行的 **Installer (安装器 / Tauri 2)** 负责在首次安装时，于本地引导 uv Python 3.13 运行时环境，解压释放 Runner wheel、默认配置及基础技能（Skills）到本地平台标准路径下。

---

## 2. 核心模块与职责矩阵 (Responsibilities)

| 维度 | Backend (云端大脑) | Desktop (本地枢纽) | Runner (本地手脚) |
|---|---|---|---|
| **运行环境** | 容器/云端服务器 (Linux Docker) | 用户本底原生环境 (Win/Mac/Linux) | 本地静默进程 (venv Python 运行时) |
| **状态持有** | 数据库（PostgreSQL）、LLM API 凭证、系统配置、用户会话历史 | 用户身份 JWT (加密)、Runner 进程 PID、本地工具集 Schema 缓存 | 终端环境快照、CDP 浏览器会话、本地 MCP Server 句柄 |
| **核心职责** | 接收用户消息、拼装多模态/长短期记忆上下文，流式调度 LLM，解析工具调用并路由 | 维护本地安全防线，中转 WS 工具帧，代理 Runner 对云端 LLM 的反向请求 | 纯粹执行底层工具逻辑，上报真实可用的工具 Schema 列表 |
| **安全准则** | `<untrusted_tool_result>` 包裹一切外部输入；Reserved 键覆盖保护；脱敏日志输出 | Renderer 进程隐藏 JWT；仅暴露本地文件系统代理拦截；屏幕录制通道防护 | Hardline 危险命令阻断；Windows 路径不敏感写限制；SSRF 防护 |

---

## 3. 通信与消息协议 (Protocols & Flows)

系统全链路采用 **JSON-RPC 2.0** 协议封装双向流量。

### 3.1 协议链路映射

#### A. Backend ↔ Desktop
使用单一 WebSocket 通道 `/api/chat/ws?token=<jwt>`。所有流式对话、控制事件、工具触发均承载在此通道中。
- **请求信封 (Request)**: `{"jsonrpc": "2.0", "id": "<call_id>", "method": "<method>", "params": {...}}`
- **事件推送 (Notification)**: `{"jsonrpc": "2.0", "method": "event", "params": {"type": "<event_type>", "payload": {...}}}`

#### B. Desktop ↔ Runner
使用本地环回 WebSocket连接 `ws://127.0.0.1:<port>/rpc`。Desktop 充当 RPC Server，Runner 在启动时作为 Client 主动连入。
- 选用 WebSocket 替代 stdio 重定向的权衡：
  1. 避免 C 库底层日志或 Python `print` 污染标准输入输出帧。
  2. 实现全双工并发，按 `id` 异步匹配响应，避免进程读写阻塞与全局锁。

---

### 3.2 核心数据流控制

#### I. 标准工具调用流 (Tool Call Flow)
当云端 LLM 决定调用本地工具（如 `terminal` 或 `write_file`）时的完整数据流向：

```
LLM (Generate tool_call)
    │
    ▼ (Stream output)
Backend [core/chat_service.py]
    │  1. 拦截 reserved 字段、检查 iteration_budget
    │  2. 创建 `ipc.create_future(user_id, call_id)`
    ▼ (WebSocket Push: method="event", type="tool.call")
Desktop [runner-bridge.cjs]
    │  3. 路由分发，由 IPC Bridge 转换帧
    ▼ (Local WS: method="execute_tool", params={"name", "args"})
Runner [server.py]
    │  4. 触发 Tirith/SSRF 扫描与文件写保护校验
    │  5. 本地执行（PTY/Playwright 等），清理控制字符与脱敏
    ▼ (Local WS Response: result={"stdout", "exit_code"})
Desktop [runner-rpc-ws.cjs]
    │  6. 捕获连接异常，若 Runner 离线快速 fail-fail
    ▼ (WebSocket Request: method="tool.result", params={"call_id", "result"})
Backend [routers/chat.py]
    │  7. 匹配 `ipc.resolve_future(user_id, call_id)` 唤醒 chat_service 协程
    ▼
LLM (Receive tool result & continue)
```

#### II. 反向 RPC 流 (Reverse RPC Flow)
本地 Runner 工具（如 `vision_analyze` 或动态 MCP）需要借助大模型“大脑”能力，但本身**不持有任何云端凭证/Token**。它通过 Desktop 代理转发：

```
Runner ──(Local WS: method="request_llm")──> Desktop ──(HTTP POST: /api/llm/completion)──> Backend ──> LLM
                                               │
                                       (Desktop JWT 鉴权)
```
- **速率守卫**：Desktop 在转发前会统计单会话请求次数与载荷大小（硬上限 200 帧 / 1MB），防止 Runner 工具代码逻辑失控无限刷 LLM 额度。

#### III. 本地文件系统代理拦截 (Local Intercepts)
对于受限于沙箱或 Docker 容器无法看到的本地环境信息，Desktop 在 Renderer 发出 WS 请求前会在本地先行拦截并处理：
- **`config.get({key: "project"})`**：由 Desktop 本地直接读取 `.git/HEAD` 恢复分支状态，不再上报给云端。
- **`complete.path({word, cwd})`**：在 `@` 路径补全时拦截，Desktop 主进程调用本地文件目录列表。

---

## 4. 事件与 Cron 调度机制 (Event Mechanics)

系统需要支持离线任务调度和事件多副本分发，不依赖死循环轮询。

### 4.1 PostgreSQL 触发器与 LISTEN/NOTIFY
为了在 Backend 多副本水平扩展部署下，实现毫秒级事件通知且无竞争重复投递，引入了 Outbox 表机制：

1. **写出事件**：后端业务或 Cron 协程（`core/cron.py` 推进 `cron_jobs` 状态）将待发送的 WS 帧写入 `ws_events` 数据表。
2. **数据库触发器**：PostgreSQL 通过 `ws_events` 上的 STATEMENT 级触发器，在写入时自动发出 `NOTIFY ws_events_channel` 信号。
3. **副本认领（Atomic Claim）**：
   - 每一个 Backend 副本独立连接并通过 `asyncpg` 监听 `LISTEN ws_events_channel`。
   - 收到唤醒后，各副本执行 `DELETE ... RETURNING` 语句。由于数据库的行锁机制，仅有一台副本能够原子获取并成功消费该行数据，然后分发给连接在此副本上的用户 WS。
   - **防阻塞机制**：调度器 tick 时只写入事件，不 await 实际的 WebSocket 发射，避免慢客户端拖垮事务执行。

---

## 5. 安全与准入控制 (Security & Trust)

### 5.1 物理与凭证隔离
- “大脑”（云端）不接触用户本地操作系统，哪怕遭遇 Prompt 注入也是在用户本机的受限账户下执行本地工具。
- “手脚”（Runner）零 Token 运行，无法越权请求 Backend 的管理级 API。
- “枢纽”（Desktop）通过 Electron 主进程 `safeStorage` 将 JWT 加密落地在 `agent-session.json` 中。Renderer 进程与 Preload 脚本无法接触 safeStorage 接口，阻断了 XSS 窃取凭证的可能性。

### 5.2 安全防护网 (Defense Layers)

#### 1. 输入清洗与指令包围 (Untrusted Wrap)
- Backend 从外部（如 Web 搜索、浏览器抓取、MCP 外部资源）获取的所有不可信文本，注入到 LLM 上下文时，均强制用 `<untrusted_tool_result>` 标签包裹。
- 注入防范：系统提示词中硬编码 `[OUT-OF-BAND USER MESSAGE]`（STEER_CHANNEL_NOTE）以对抗恶意提示词篡改。
- 工具预留键防注入：禁止大模型在工具入参里恶意覆盖 `user_id` / `llm_config` / `user_settings`。

#### 2. 本地执行拦截 (Soft & Hard Blocks)
- **硬阻断 (Hard Blocks - utils/file_safety.py)**：
  - 严禁修改或读取系统级别及凭证文件（如 `.ssh/`、AWS/GCP credentials、`/etc/passwd` 等）。
  - **Windows 路径归一化**：在 Windows 平台上比对前缀时，将路径统一转为斜杠 `/` 并强制小写（例如 `C:\Windows` ↔ `c:/windows`），同时自动扫描并锁定所有动态挂载盘符。
- **软防线 (Soft Guards - file_tools.py)**：
  - 针对三类跨域写操作抛出 LLM 可见的警告信息，只有用户显式许可并在入参中注入 `cross_profile=True` 才会放行：
    1. `cross-profile`：写入另一个用户的 Skills/Memories 配置。
    2. `sandbox-mirror`：写入主机侧绑定的容器任务镜像路径。
    3. `container-mirror`：写入容器内部被重定向的 `/root/` 敏感路径。

#### 3. 动态扫描与防护 (Tirith & SSRF)
- **Tirith 扫描**：Runner 在执行任何 shell 命令（`terminal` 工具）之前，拉起本地 `tirith` 扫描模块对参数进行签名与静态审查。
- **SSRF 拦截**：在 `browser_*` 导航或 `send_message` Webhook 触发前，自动调用 `getaddrinfo` 解析目标 IP，强制阻断对 loopback (127.0.0.1)、私有子网 (RFC 1918)、CGNAT、以及云服务商 Metadata (169.254.169.254) 的物理连接。

---

## 6. 错误契约与韧性设计 (Error Contract)

### 6.1 错误分层模型

```
┌────────────────────────────────────────────────────────────────┐
│   REST API 异常 / WebSocket 联通断开                           │
│   - REST 返回 {error, reason, status} 结构                     │
│   - WebSocket 关闭码感知：若是 1008 (鉴权失效) 立即退出重连流程   │
└──────────────────────────────┬─────────────────────────────────┘
                               │
┌──────────────────────────────▼─────────────────────────────────┐
│   JSON-RPC 协议异常                                             │
│   - 标准 JSON-RPC 2.0 错误码 (-32700 到 -32603)                 │
│   - -32603 (内部错误) 抛至客户端前做脱敏隔离，不暴露栈底敏感路径 │
└──────────────────────────────┬─────────────────────────────────┘
                               │
┌──────────────────────────────▼─────────────────────────────────┐
│   Runner 本地工具执行异常                                       │
│   - 返回 {ok: false, result: None, error: "<redacted_error>"}  │
│   - 进行全局 regex 脱敏，过滤包含 sk- / ghp_ / 私钥等凭证内容    │
└────────────────────────────────────────────────────────────────┘
```

### 6.2 错误分类管道 (Failover Reason)
云端 `error_classifier.py` 构筑了 8 步优先级过滤（Provider 规则 ↔ 状态码 ↔ 错误代码 ↔ 异常特征 ↔ SSL 传输 ↔ 断连 ↔ 启发式），将所有 API 层面或依赖项错误收拢为 21 种 `FailoverReason`：
- **计费与限流** (`billing`, `rate_limit`)：触发客户端自动退避重试或主动切换凭证。
- **附件获取异常** (`attachment_fetch_failed`)：当 LLM 获取多模态图片/视频附件失败时，拦截 Proxy 端生成的原始 SDK 报错，向用户返回独立、明确的 Proxy 接入报错，避免误导性地触发 LLM 回退逻辑。

---

## 7. 自更新生命周期 (Self-Update)

Desktop 的更新不依赖 Git 源码，而是通过 `/api/update` 获取 Electron 二进制与 Runner wheel 二进制包，并在本地进行原子替换。为了确保升级中途断网或崩溃不会导致程序“变砖”，系统设计了**两阶段自更新契约**：

```
[OLD Electron 运行中]
  │
  ├─ 1. 定时后台自检最新版本 (30s)
  ├─ 2. 下载新版 Electron 二进制，同时触发 Runner Staging Prefetch
  ├─ 3. 将新版 Runner wheel 和 server.py 下载至 $ZAST_HOME/runner.staging/
  └─ 4. 强校验公钥签名 (update.pub) 与 SHA-512，写入升级 Sentinel 标记
  │
[用户点击重启 (Restart & Install)]
  │
[NEW Electron 引导 Ready]
  │
  ├─ 5. 读取 Sentinel，调用 `runnerBridge.stop()` 释放旧 Python 句柄
  ├─ 6. 执行 `pip install --upgrade "<wheel>"` 在现有 venv 中原地覆盖升级
  ├─ 7. 覆盖 server.py 并进行 Python 导入冒烟测试
  ├─ 8. 成功 -> 启动 Runner 正常工作
  └─ 失败 -> 触发回滚分支，清理 Sentinel，以旧版 Runner 降级启动并向用户发出警告
```
- **核心约束**：Runner 的 venv 目录永远不作重命名或移动，确保任意升级阶段崩溃时，旧版 Runner 依赖树在本地依然是完全可用的。

---

## 8. 全局开发不变量 (Invariants)

1. **Runner 零凭证**：Runner 进程不能被授信任何 Backend Token，所有出站/ LLM 请求必须向上借道 Desktop IPC 进行中转代理。
2. **错误遮蔽**：抛出到前端的 -32603 错误信息必须经过脱敏清洗，严禁将包含数据库账号、服务器本地文件路径等栈帧细节输出。
3. **两阶段更新强校验**：禁止绕过 Stage 1 的 prefetch 逻辑直接覆盖本地 Runner 文件；公钥签名不匹配的升级包在 Staging 阶段直接拦截。
4. **ID 职责分立**：在通信协议边界必须完成整型 `conversation_id` 与字符串 `session_id` 的显式转换。`call_id` 作为唯一 Future Key 标识生命周期。
5. **平台兼容性防御**：在 Windows 下进行进程控制或路径读取时，必须采用 `utils/pid.py` 内的 `psutil`/`taskkill` 原生封装，防止 Windows PTY 进程链悬挂。

---

## 9. 子模块详细 CLAUDE.md 开发索引

- **Backend (云端大脑) 逻辑与 DB 模型**：[backend/CLAUDE.md](file:///c:/Code/zast-agent/backend/CLAUDE.md)
- **Runner (本地手脚) 执行器与工具库**：[runner/CLAUDE.md](file:///c:/Code/zast-agent/runner/CLAUDE.md)
- **Desktop (本地枢纽) IPC 命名空间与自更新**：[desktop/CLAUDE.md](file:///c:/Code/zast-agent/desktop/CLAUDE.md)
- **Installer (安装器) 引导协议与环境分发**：[installer/CLAUDE.md](file:///c:/Code/zast-agent/installer/CLAUDE.md)
- **Scripts (发布与集成) 构建与导入规范检查**：[scripts/CLAUDE.md](file:///c:/Code/zast-agent/scripts/CLAUDE.md)
