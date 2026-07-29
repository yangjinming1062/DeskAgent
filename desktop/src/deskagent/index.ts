// Type re-exports (single source of truth: @/types/deskagent)
export type {
  ActionStatusResponse,
  DeskAgentConfigPutRequest,
  DeskAgentConfigRecord,
  DeskAgentConfigResponse,
  InsightsDailyActivity,
  InsightsModel,
  InsightsOverview,
  InsightsOverviewMetrics,
  InsightsPlatform,
  InsightsSkillSummary,
  InsightsSkillTag,
  InsightsTopTool,
  ModelConfigResponse,
  PaginatedSessions,
  RpcEvent,
  SessionCreateResponse,
  SessionInfo,
  SessionMessage,
  SessionMessagesResponse,
  SessionResumeResponse,
  SessionRuntimeInfo,
  SessionSearchResponse,
  SessionSearchResult,
  StatusResponse,
  UsageStats
} from './_types'

export { getDeskAgentConfig, getDeskAgentConfigDefaults, getDeskAgentConfigRecord, pickSection, saveDeskAgentConfig } from './config'
// Gateway class
export { DeskAgentGateway } from './gateway'
// Insights
export { getInsightsOverview } from './insights'
// Domain functions
export {
  deleteSession,
  getSessionMessages,
  listSessions,
  renameSession,
  searchSessions,
  setSessionArchived
} from './sessions'
// Status probe
export { getStatus } from './status'

export async function transcribeAudio(
  dataUrl: string,
  _mimeType?: string
): Promise<{ ok: boolean; transcript: string }> {
  try {
    const { text } = await window.deskagent.media.stt({ dataUrl })

    return { ok: true, transcript: text }
  } catch (error) {
    return { ok: false, transcript: (error as Error).message ?? 'transcribe failed' }
  }
}

export async function speakText(text: string): Promise<{ ok: boolean; data_url: string; mime_type: string }> {
  try {
    const { dataUrl, mimeType } = await window.deskagent.media.tts({ text })

    return { ok: true, data_url: dataUrl, mime_type: mimeType }
  } catch {
    return { ok: false, data_url: '', mime_type: '' }
  }
}
