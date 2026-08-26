import type { RequestGateway, VoiceOption } from '@/shared/voice-catalog'

// 音色目录、匹配与设计由后端 `tts.*` JSON-RPC 方法
// （services/companion/voice_catalog.py）支撑。音色 id 属于供应商私有命名空间；
// 后端目录只列出当前 TTS 供应商接受的 id，所以匹配到的 id 永远不会让合成失败。

export type { VoiceOption } from '@/shared/voice-catalog'
export { GENDER_OPTIONS, LANGUAGE_LABELS } from '@/shared/voice-catalog'

interface VoiceMatch {
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

const DEFAULT_VOICE: VoiceOption = {
  id: '',
  label: '默认音色',
  gender: 'neutral',
  language: '',
  tags: ['默认'],
  description: '使用引擎默认音色。'
}

export function playDataUrl(dataUrl: string): void {
  void new Audio(dataUrl).play().catch(() => {})
}

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

type FetchResult = { ok: true; catalog: VoiceCatalog } | { ok: false; reason: 'fetch_failed' | 'empty_catalog' }

export async function fetchVoiceCatalogRaw(
  requestGateway: RequestGateway,
  language?: string | null
): Promise<FetchResult> {
  try {
    const res = await requestGateway<CatalogResponse>('tts.list_voices', {
      language: language ?? null
    })

    const voices = res.voices?.length ? res.voices : [DEFAULT_VOICE]

    return {
      ok: true,
      catalog: {
        provider: res.provider ?? '',
        voices,
        defaultVoice: res.default_voice ?? DEFAULT_VOICE,
        supportsVoiceDesign: Boolean(res.supports_voice_design),
        voiceDesignGuide: res.voice_design_guide ?? ''
      }
    }
  } catch {
    return { ok: false, reason: 'fetch_failed' }
  }
}

export async function matchVoicePreference(requestGateway: RequestGateway, preference: string): Promise<VoiceMatch> {
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
  if (catalog.length <= 1) {
    return catalog[0] ?? DEFAULT_VOICE
  }

  const idx = catalog.findIndex(v => v.id === currentId)

  return catalog[(idx + 1) % catalog.length] ?? catalog[0]
}

export function sampleLine(name: string): string {
  return `你好呀，我是${name || ''}。这是我的声音～`
}
