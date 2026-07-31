// Voice catalog + matching backed by the Backend `tts.list_voices` /
// `tts.match_voice` JSON-RPC methods (services/companion/voice_catalog.py).
// Voice ids are provider-specific; the backend catalog only lists ids the
// active TTS provider accepts, so a matched id never breaks synthesis.

export interface VoiceOption {
  id: string
  label: string
  gender: string
  tags: readonly string[]
  description: string
}

export interface VoiceMatch {
  voice: VoiceOption
  alternatives: VoiceOption[]
}

export const DEFAULT_VOICE: VoiceOption = {
  id: '',
  label: '默认音色',
  gender: 'neutral',
  tags: ['默认'],
  description: '使用引擎默认音色。'
}

type RequestGateway = <T>(method: string, params?: Record<string, unknown>) => Promise<T>

interface CatalogResponse {
  provider?: string
  voices?: VoiceOption[]
}

interface MatchResponse {
  voice?: VoiceOption
  alternatives?: VoiceOption[]
}

export async function fetchVoiceCatalog(requestGateway: RequestGateway): Promise<VoiceOption[]> {
  try {
    const res = await requestGateway<CatalogResponse>('tts.list_voices', {})

    return res.voices?.length ? res.voices : [DEFAULT_VOICE]
  } catch {
    return [DEFAULT_VOICE]
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

export function nextVoice(currentId: string, catalog: readonly VoiceOption[]): VoiceOption {
  if (catalog.length <= 1) {return catalog[0] ?? DEFAULT_VOICE}
  const idx = catalog.findIndex(v => v.id === currentId)

  return catalog[(idx + 1) % catalog.length] ?? catalog[0]
}

export function sampleLine(name: string): string {
  return `你好呀，我是${name || ''}。这是我的声音～`
}
