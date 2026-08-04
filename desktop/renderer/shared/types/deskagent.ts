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
}

export interface SessionRuntimeInfo {
  branch?: string
  config_warning?: string
  credential_warning?: string
  cwd?: string
  desktop_contract?: number
  fast?: boolean
  model?: string
  personality?: string
  provider?: string
  reasoning_effort?: string
  running?: boolean
  service_tier?: string
  skills?: Record<string, string[]> | string[]
  tools?: Record<string, string[]>
  usage?: Partial<UsageStats>
  version?: string
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

/** STT/TTS engine routing preference, resolved by the Desktop main process.
 * `auto` = local Runner engine first with cloud fallback; `local` = local only
 * (no cloud fallback); `cloud` = backend always. See desktop/main/ipc/media.cjs. */
export type SpeechEngine = 'auto' | 'local' | 'cloud'

/** Shape returned by `GET /api/config`. Includes computed siblings like `*_set` / `*_fingerprint`
 * that the backend injects after stripping raw credentials. */
export interface DeskAgentConfigResponse {
  agent?: {
    reasoning_effort?: string
    personalities?: Record<string, unknown>
    service_tier?: string
    enable_background_review?: boolean
  }
  display?: {
    personality?: string
    skin?: string
    /** Hides subagent conversations from the sidebar by default. */
    show_subagents_in_sidebar?: boolean
  }
  mcp_servers?: Record<string, Record<string, unknown>>
  terminal?: {
    cwd?: string
  }
  stt?: {
    enabled?: boolean
    engine?: SpeechEngine
    /** When false, a weak/low-confidence local STT result surfaces to the user
     * instead of silently retrying on cloud. Default true. See media.cjs. */
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
    brave_api_key_set?: boolean
    brave_api_key_fingerprint?: string
    tavily_api_key_set?: boolean
    tavily_api_key_fingerprint?: string
    tavily_base_url?: string
  }
}

/** Body shape accepted by `PUT /api/config`. Raw credentials are writable here; the computed
 * `*_set` / `*_fingerprint` siblings from the response shape are not. */
export interface DeskAgentConfigPutRequest {
  agent?: {
    reasoning_effort?: string
    personalities?: Record<string, unknown>
    service_tier?: string
    enable_background_review?: boolean
  }
  display?: {
    personality?: string
    skin?: string
    show_subagents_in_sidebar?: boolean
  }
  mcp_servers?: Record<string, Record<string, unknown>>
  terminal?: {
    cwd?: string
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
    /** Empty string clears the key; omitting the field leaves the existing value untouched. */
    brave_api_key?: string
    /** Empty string clears the key; omitting the field leaves the existing value untouched. */
    tavily_api_key?: string
    tavily_base_url?: string
  }
}

export type DeskAgentConfigRecord = Record<string, unknown>

/** LLM model config projected by the `deskagent:model-config:get` IPC.
 *
 * GCS secrets (`gcs_access_key` / `gcs_secret_key` / `gcs_bucket_name`) are
 * intentionally NOT included — they are stripped at the IPC boundary in
 * `main/ipc/auth.cjs::deskagent:model-config:get`. The full object (with GCS
 * fields) lives only in main-process code paths that need it for uploads.
 */
export interface ModelConfigResponse {
  llm_api_key_fingerprint: string
  llm_api_key_set: boolean
  llm_base_url: string
  llm_model_name: string
}

/** Shape returned by `GET /api/insights/overview?days=N`.
 *
 * Field names mirror the backend's `insights.py` response — kept snake_case
 * to match the JSON-RPC / REST conventions used by other endpoints in this
 * module (see `SessionInfo`, `UsageStats`).
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
