export interface OnboardingAnswers {
  name?: string
  species?: string
  character_gender?: string
  appearance?: string
  role?: string
  personality?: string
  // 1-6 (backend audit): user-picked speaking style (chip + free-text).
  // When non-empty, overrides the personality-key derivation in
  // `assemblePersona` so the user is the source of truth.
  speaking_style?: string
  user_call_name?: string
  user_gender?: string
  user_age_bucket?: string
  user_hobbies?: string
  user_freeform?: string
  voice?: string
}

export interface PersonaPayload {
  name: string
  personality: string
  speaking_style: string
  background?: string
  biological_type?: string
  gender?: string
  appearance?: string
  user_call_name?: string
  user_gender?: string
  user_age_bucket?: string
  user_hobbies?: string
  user_freeform?: string
}

const DEFAULT_PERSONALITY = '温柔体贴'
export const MAX_APPEARANCE = 500
export const MAX_USER_TEXT = 2000
const MAX_SPECIES_GENDER = 64
const MAX_BACKGROUND = 500

function truncate(value: string | undefined, max: number): string | undefined {
  if (!value) {return undefined}
  const trimmed = value.trim()

  if (!trimmed) {return undefined}

  return trimmed.slice(0, max)
}

// speaking_style must reflect a real user selection: presets map to a fixed
// style; free-text personality falls back to the default so the schema holds.
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

  // 1-6: user-picked speaking_style (chip + free-text) wins over the
  // personality-key derivation when both are present. If the user
  // skipped Q13, fall back to the persona-key map so the persona
  // still satisfies the backend's required speaking_style field.
  const userPickedStyle = answers.speaking_style?.trim()

  const payload: PersonaPayload = {
    name,
    personality,
    speaking_style: userPickedStyle || deriveSpeakingStyle(answers.role, answers.personality),
  }

  const optional: Array<[keyof PersonaPayload, string | undefined, number]> = [
    ['biological_type', answers.species, MAX_SPECIES_GENDER],
    ['gender', answers.character_gender, MAX_SPECIES_GENDER],
    ['appearance', answers.appearance, MAX_APPEARANCE],
    ['background', answers.role, MAX_BACKGROUND],
    ['user_call_name', answers.user_call_name, MAX_USER_TEXT],
    ['user_gender', answers.user_gender, MAX_USER_TEXT],
    ['user_age_bucket', answers.user_age_bucket, MAX_USER_TEXT],
    ['user_hobbies', answers.user_hobbies, MAX_USER_TEXT],
    ['user_freeform', answers.user_freeform, MAX_USER_TEXT],
  ]

  for (const [key, raw, max] of optional) {
    const trimmed = truncate(raw, max)

    if (trimmed) {payload[key] = trimmed}
  }

  return payload
}
