// Desktop-first mocks for Backend companion capabilities that don't exist yet
// (see plan.md §5). Voice matching has no Backend implementation — these are
// UI labels only; actual TTS audio uses the provider's default voice until the
// Backend gains a voice-selection endpoint. Toggled off in production builds
// once the real endpoints ship.

export interface VoiceOption {
  id: string
  label: string
  tags: string[]
}

export const VOICE_OPTIONS: readonly VoiceOption[] = [
  { id: 'female-gentle', label: '温柔少女音', tags: ['温柔', '少女', '轻柔', '女'] },
  { id: 'male-calm', label: '沉稳男声', tags: ['沉稳', '男声', '成熟', '磁性'] },
  { id: 'boy-lively', label: '活泼正太', tags: ['活泼', '正太', '少年', '正'] },
  { id: 'female-cool', label: '清冷御姐', tags: ['清冷', '御姐', '御', '姐'] }
]

export function matchVoice(preference: string | undefined): VoiceOption {
  const p = (preference || '').toLowerCase()

  return (
    VOICE_OPTIONS.find(v => v.tags.some(t => p.includes(t)) || p.includes(v.label)) ?? VOICE_OPTIONS[0]
  )
}

export function nextVoice(currentId: string): VoiceOption {
  const idx = VOICE_OPTIONS.findIndex(v => v.id === currentId)

  return VOICE_OPTIONS[(idx + 1) % VOICE_OPTIONS.length]
}

export function sampleLine(name: string): string {
  return `你好呀，我是${name || ''}。这是我的声音～`
}
