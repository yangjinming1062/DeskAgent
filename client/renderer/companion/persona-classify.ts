// Canonical personality-key classifier. Both `deriveSpeakingStyle` (which
// resolves a Snake_case speaking style for the persona payload) and
// `personaTone` (which selects reaction tone enum for interaction.ts) are
// downstream of this function. Adding a new personality key requires only
// one new entry here.

export type PersonalityClass = 'snarky' | 'lively' | 'calm' | 'gentle'

export function classifyPersonality(personality: string | undefined, role?: string): PersonalityClass {
  const p = (personality ?? '').toLowerCase()

  if (p.includes('毒舌') || p.includes('傲娇')) {
    return 'snarky'
  }

  if (p.includes('活泼') || p.includes('好动')) {
    return 'lively'
  }

  if (p.includes('冷静') || p.includes('理性')) {
    return 'calm'
  }

  if (role) {
    const r = role.toLowerCase()

    if (r.includes('管家') || r.includes('秘书') || r.includes('贾维斯')) {
      return 'calm'
    }
  }

  return 'gentle'
}

const SPEAKING_STYLE_BY_CLASS: Record<PersonalityClass, string> = {
  snarky: '俏皮带点小傲娇',
  lively: '轻快活泼',
  calm: '沉稳简洁',
  gentle: '温柔亲切'
}

// `calm` is overridden by the role-key derivation in `calm` bucket.
const CALM_ROLE_STYLE = '专业干练'

export function deriveSpeakingStyle(role: string | undefined, personality: string | undefined): string {
  const klass = classifyPersonality(personality, role)

  if (klass === 'calm' && role && (role.includes('管家') || role.includes('秘书') || role.includes('贾维斯'))) {
    return CALM_ROLE_STYLE
  }

  return SPEAKING_STYLE_BY_CLASS[klass]
}
