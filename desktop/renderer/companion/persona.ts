// Assemble the Backend PersonaUpdate payload from onboarding answers.
// Backend's PersonaUpdate requires name/personality/speaking_style and rejects
// unknown keys (extra="forbid"). selfIntro is USER info → memory layer (NOT
// persona, per design.md §7.6); voice feeds TTS, not persona. Skipped required
// fields get sensible defaults so the PUT always succeeds (is_complete=true),
// unblocking Slice 3 chat with a personality injected. See plan.md §4.

export interface OnboardingAnswers {
  name?: string
  role?: string
  personality?: string
  selfIntro?: string
  voice?: string
}

export interface PersonaPayload {
  name: string
  personality: string
  speaking_style: string
  background?: string
}

const DEFAULT_PERSONALITY = '温柔体贴'

export function deriveSpeakingStyle(role: string | undefined, personality: string | undefined): string {
  const p = personality || ''

  if (p.includes('毒舌') || p.includes('傲娇')) {return '俏皮带点小傲娇'}

  if (p.includes('冷静') || p.includes('理性')) {return '沉稳简洁'}

  if (p.includes('活泼')) {return '轻快活泼'}

  if (role && (role.includes('管家') || role.includes('秘书') || role.includes('贾维斯'))) {return '专业干练'}

  return '温柔亲切'
}

export function assemblePersona(answers: OnboardingAnswers): PersonaPayload {
  const name = answers.name?.trim() || '伙伴'
  const personality = answers.personality?.trim() || DEFAULT_PERSONALITY

  const payload: PersonaPayload = {
    name,
    personality,
    speaking_style: deriveSpeakingStyle(answers.role, answers.personality)
  }

  const background = answers.role?.trim()

  if (background) {payload.background = background}

  return payload
}
