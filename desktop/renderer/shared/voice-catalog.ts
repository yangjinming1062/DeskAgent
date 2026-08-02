// Voice catalog types + labels shared between the companion sprite window
// (which fetches via JSON-RPC) and the hub tool window (which fetches via
// REST). Both reach the same Backend catalog; only the transport and the
// gateway-side snake→camel mapping differ.

export interface VoiceOption {
  id: string
  label: string
  gender: string
  language: string
  tags: readonly string[]
  description: string
}

export const LANGUAGE_LABELS: Record<string, string> = {
  zh: '中文',
  en: '英文',
  multi: '多语言',
  '': '通用'
}

export const GENDER_OPTIONS: { id: string; label: string }[] = [
  { id: '', label: '全部' },
  { id: 'female', label: '女声' },
  { id: 'male', label: '男声' },
  { id: 'neutral', label: '中性' }
]

// Prefix used by MiMoTTSProvider.synthesize to encode a designed voice as a
// single string (`mimo_voicedesign:<prompt>`). Recognized in two places: the
// renderer-side validity check (companion/voice-validity.ts) and the
// main-process TTS router (media.cjs), which must force design tokens to
// cloud regardless of the user's local-vs-cloud preference.
export const VOICEDESIGN_PREFIX = 'mimo_voicedesign:'

export type RequestGateway = <T>(method: string, params?: Record<string, unknown>) => Promise<T>
