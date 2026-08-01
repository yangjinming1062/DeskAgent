// Voice catalog + matching + design backed by the Backend `tts.*` JSON-RPC
// methods (services/companion/voice_catalog.py). Voice ids are provider-specific;
// the backend catalog only lists ids the active TTS provider accepts, so a
// matched id never breaks synthesis.

export interface VoiceOption {
  id: string
  label: string
  gender: string
  language: string
  tags: readonly string[]
  description: string
}

export interface VoiceMatch {
  voice: VoiceOption
  alternatives: VoiceOption[]
}

export interface VoiceCatalog {
  provider: string
  voices: VoiceOption[]
  defaultVoice?: VoiceOption
  supportsVoiceDesign: boolean
  voiceDesignGuide: string
}

export interface VoiceDesignPreview {
  voiceId: string
  trialAudioDataUrl: string
}

export const DEFAULT_VOICE: VoiceOption = {
  id: '',
  label: '默认音色',
  gender: 'neutral',
  language: '',
  tags: ['默认'],
  description: '使用引擎默认音色。'
}

export const EMPTY_CATALOG: VoiceCatalog = {
  provider: '',
  voices: [DEFAULT_VOICE],
  supportsVoiceDesign: false,
  voiceDesignGuide: ''
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

export function playDataUrl(dataUrl: string): void {
  void new Audio(dataUrl).play().catch(() => {})
}

type RequestGateway = <T>(method: string, params?: Record<string, unknown>) => Promise<T>

interface CatalogResponse {
  provider?: string
  voices?: VoiceOption[]
  default_voice?: VoiceOption
  supports_voice_design?: boolean
  voice_design_guide?: string
}

interface MatchResponse {
  voice?: VoiceOption
  alternatives?: VoiceOption[]
}

interface DesignResponse {
  voice_id: string
  trial_audio_base64: string
  trial_audio_mime: string
}

export async function fetchVoiceCatalog(
  requestGateway: RequestGateway,
  language?: string | null
): Promise<VoiceCatalog> {
  try {
    const res = await requestGateway<CatalogResponse>('tts.list_voices', {
      language: language ?? null
    })
    const voices = res.voices?.length ? res.voices : [DEFAULT_VOICE]

    return {
      provider: res.provider ?? '',
      voices,
      defaultVoice: res.default_voice ?? DEFAULT_VOICE,
      supportsVoiceDesign: Boolean(res.supports_voice_design),
      voiceDesignGuide: res.voice_design_guide ?? ''
    }
  } catch {
    return EMPTY_CATALOG
  }
}

export async function matchVoicePreference(
  requestGateway: RequestGateway,
  preference: string
): Promise<VoiceMatch> {
  try {
    const res = await requestGateway<MatchResponse>('tts.match_voice', { preference })
    const voice = res.voice ?? DEFAULT_VOICE

    return { voice, alternatives: res.alternatives ?? [] }
  } catch {
    return { voice: DEFAULT_VOICE, alternatives: [] }
  }
}

export async function designVoice(
  requestGateway: RequestGateway,
  prompt: string,
  previewText = ''
): Promise<VoiceDesignPreview> {
  const res = await requestGateway<DesignResponse>('tts.design_voice', { prompt, preview_text: previewText })

  return {
    voiceId: res.voice_id,
    trialAudioDataUrl: `data:${res.trial_audio_mime};base64,${res.trial_audio_base64}`
  }
}

export function nextVoice(currentId: string, catalog: readonly VoiceOption[]): VoiceOption {
  if (catalog.length <= 1) {return catalog[0] ?? DEFAULT_VOICE}
  const idx = catalog.findIndex(v => v.id === currentId)

  return catalog[(idx + 1) % catalog.length] ?? catalog[0]
}

export function sampleLine(name: string): string {
  return `你好呀，我是${name || ''}。这是我的声音～`
}
