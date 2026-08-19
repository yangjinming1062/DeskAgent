export interface ActionStatusResponse {
  exit_code: number | null
  lines: string[]
  name: string
  pid: number | null
  running: boolean
}

export interface ModelPricing {
  input: string
  output: string
  cache: string | null
  free: boolean
}

export interface PaginatedSessions {
  limit: number
  offset: number
  sessions: SessionInfo[]
  total: number
}

export interface RpcEvent<T = unknown> {
  payload?: T
  session_id?: string
  type: string
}

export interface SessionCreateResponse {
  info?: SessionRuntimeInfo
  message_count?: number
  messages?: SessionMessage[]
  session_id: string
}

export interface SessionInfo {
  archived?: boolean
  /** 服务端是自由字符串；目前只有 'main' 和 'standard' 是一级值。 */
  kind?: string
  cwd?: null | string
  ended_at: null | number
  id: string
  _lineage_root_id?: null | string
  input_tokens: number
  is_active: boolean
  last_active: number
  message_count: number
  model: null | string
  output_tokens: number
  preview: null | string
  source: null | string
  started_at: number
  title: null | string
  tool_call_count: number
  handoff_platform?: null | string
  handoff_state?: null | string
  handoff_error?: null | string
}

export interface SessionMessage {
  codex_reasoning_items?: unknown
  content: unknown
  context?: unknown
  name?: string
  reasoning?: null | string
  reasoning_content?: null | string
  reasoning_details?: unknown
  role: 'assistant' | 'system' | 'tool' | 'user'
  subtype?: string
  text?: unknown
  timestamp?: number
  tool_call_id?: null | string
  tool_calls?: unknown
  tool_name?: string
}

export interface SessionMessagesResponse {
  messages: SessionMessage[]
  session_id: string
}

export interface SessionResumeResponse {
  info?: SessionRuntimeInfo
  message_count: number
  messages: SessionMessage[]
  session_id: string
  resumed?: boolean
  replayed_count?: number
  current_seq?: number
}

export interface SessionRuntimeInfo {
  branch?: string
  cwd?: string
  model?: string
  provider?: string
  running?: boolean
}

export interface SessionSearchResult {
  lineage_root?: string | null
  model: string | null
  role: string | null
  session_id: string
  session_started: number | null
  snippet: string
  source: string | null
}

export interface SessionSearchResponse {
  results: SessionSearchResult[]
}

export interface StatusResponse {
  login_count: number
  chat_count: number
  connection_state: 'connected' | 'disconnected'
}

export interface UsageStats {
  calls: number
  context_max?: number
  context_percent?: number
  context_used?: number
  cost_usd?: number
  input: number
  output: number
  total: number
}

/** STT/TTS 引擎路由偏好，由桌面端主进程解析。
 * `auto` = 优先本地 Runner 引擎，云端兜底；`local` = 仅本地（无云端兜底）；
 * `cloud` = 始终走后端。见 client/main/ipc/media.cjs。 */
export type SpeechEngine = 'auto' | 'local' | 'cloud'

/** `GET /api/config` 的返回结构。后端剥离原始凭据后注入 `*_set` / `*_fingerprint`
 * 等计算字段。 */
export interface SpiritAgentConfigResponse {
  agent?: {
    reasoning_effort?: string
    service_tier?: string
    enable_background_review?: boolean
  }
  chat?: {
    enable_context_compression?: boolean
    context_compression_threshold?: number
  }
  stt?: {
    /** 总开关——为 false 时，spiritagent:media:stt 直接拒绝，
     * 不进行任何本地 / 云端工作。见 media.cjs。 */
    enabled?: boolean
    engine?: SpeechEngine
    /** 为 false 时，低置信度的本地 STT 结果直接展示给用户，
     * 而非悄悄用云端重试。默认 true。见 media.cjs。 */
    silent_fallback?: boolean
  }
  tts?: {
    engine?: SpeechEngine
  }
  voice?: {
    /** 单次语音录制的最长时长（秒）。聊天面板在该上限处自动停止 MediaRecorder。 */
    max_recording_seconds?: number
  }
  web?: {
    backend?: string
    extract_backend?: string
    brave_api_key_set?: boolean
    brave_api_key_fingerprint?: string
    tavily_api_key_set?: boolean
    tavily_api_key_fingerprint?: string
    tavily_base_url?: string
  }
}

/** `PUT /api/config` 接受的请求体。原始凭据可写入；响应结构里的
 * `*_set` / `*_fingerprint` 计算字段不可写入。 */
export interface SpiritAgentConfigPutRequest {
  agent?: {
    reasoning_effort?: string
    service_tier?: string
    enable_background_review?: boolean
  }
  chat?: {
    enable_context_compression?: boolean
    context_compression_threshold?: number
  }
  stt?: {
    enabled?: boolean
    engine?: SpeechEngine
    silent_fallback?: boolean
  }
  tts?: {
    engine?: SpeechEngine
  }
  voice?: {
    max_recording_seconds?: number
  }
  web?: {
    backend?: string
    extract_backend?: string
    /** 空字符串清空该 key；省略字段则保持原值不变。 */
    brave_api_key?: string
    /** 空字符串清空该 key；省略字段则保持原值不变。 */
    tavily_api_key?: string
    tavily_base_url?: string
  }
}

export type SpiritAgentConfigRecord = Record<string, unknown>

/** `GET /api/insights/overview?days=N` 的返回结构。
 *
 * 字段名与服务端 `insights.py` 响应保持一致——使用 snake_case，
 * 以匹配本模块其他端点采用的 JSON-RPC / REST 命名约定（见 `SessionInfo`、`UsageStats`）。
 */
export interface InsightsTopTool {
  count: number
  tool: string
}

export interface InsightsModel {
  base_url: string
  is_active: boolean
  model: string
}

export interface InsightsPlatform {
  count: number
  pct: number
  platform: string
}

export interface InsightsSkillTag {
  count: number
  tag: string
}

export interface InsightsSkillSummary {
  new_in_window: number
  top_tags: InsightsSkillTag[]
  total_memories: number
}

export interface InsightsDailyActivity {
  date: string
  messages: number
}

export interface InsightsOverviewMetrics {
  avg_session_duration: number
  total_hours: number
  total_input_tokens: number
  total_messages: number
  total_output_tokens: number
  total_sessions: number
  total_tool_calls: number
  total_tokens: number
}

export interface InsightsOverview {
  activity: InsightsDailyActivity[]
  days: number
  models: InsightsModel[]
  overview: InsightsOverviewMetrics
  platforms: InsightsPlatform[]
  skills: InsightsSkillSummary
  top_tools: InsightsTopTool[]
}
