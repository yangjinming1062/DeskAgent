export interface RpcEvent<T = unknown> {
  payload?: T
  session_id?: string
  type: string
}

export interface SessionInfo {
  archived?: boolean
  pinned?: boolean
  /** 服务端是自由字符串；一级值有 'main'、'standard' 与 'im'（IM 桥接会话，桌面端只读）。 */
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

/** 助手消息附带的生成媒体；与正文正交，仅渲染端消费。 */
export interface ChatMediaItem {
  type: 'image' | 'video'
  url: string
}

/** 用户侧聊天附件：图片为 data URL，视频为后端上传返回的会话级 URL；水合与发送共用同一形状。 */
export interface ChatAttachment {
  type: 'image' | 'video'
  url: string
}

export interface SessionMessage {
  codex_reasoning_items?: unknown
  content: unknown
  context?: unknown
  media?: ChatMediaItem[]
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
  settings?: Record<string, unknown>
  context_window?: number
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
    enable_background_review?: boolean
    temperature?: number
  }
  chat?: {
    enable_context_compression?: boolean
    context_compression_threshold?: number
    title_generation_temperature?: number
    compression_temperature?: number
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
    enable_background_review?: boolean
    temperature?: number
  }
  chat?: {
    enable_context_compression?: boolean
    context_compression_threshold?: number
    title_generation_temperature?: number
    compression_temperature?: number
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

/** IM 通道桥（/api/channels）——绑定状态视图；凭据字段服务端永不回显。 */
export interface ChannelCapabilities {
  supports_typing: boolean
  supports_media: boolean
  can_initiate: boolean
  requires_login: boolean
}

export interface ChannelBindingInfo {
  status: string
  account_ref: string
  account_name: string
  conversation_id: number | null
  last_error: string | null
  updated_at: string | null
}

export interface ChannelInfo {
  channel: string
  title: string
  capabilities: ChannelCapabilities
  binding: ChannelBindingInfo | null
}

export interface ChannelListResponse {
  items: ChannelInfo[]
}

/** 扫码登录轮询：state ∈ idle|wait|scaned|confirmed|expired|error|login_required|connected。 */
export interface ChannelLoginState {
  state: string
  qr_image?: string | null
  error?: string | null
}

export interface ChannelPeerInfo {
  peer_id: string
  peer_name: string
  status: 'allowed' | 'blocked' | 'pending'
  last_message_at: string | null
}

export interface ChannelPeersResponse {
  items: ChannelPeerInfo[]
}

export type ChannelPeerAction = 'approve' | 'block' | 'delete'
