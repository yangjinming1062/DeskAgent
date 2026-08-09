export interface OnboardingAnswers {
  name?: string
  species?: string
  character_gender?: string
  appearance?: string
  role?: string
  personality?: string
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
  if (!value) {
    return undefined
  }

  const trimmed = value.trim()

  if (!trimmed) {
    return undefined
  }

  return trimmed.slice(0, max)
}

export function assemblePersona(answers: OnboardingAnswers): PersonaPayload {
  const name = answers.name?.trim() || '伙伴'
  const personality = answers.personality?.trim() || DEFAULT_PERSONALITY

  // 客户端仅透传用户输入，不进行程序化转换；未填写时以性格设定兜底
  const userPickedStyle = answers.speaking_style?.trim()
  const speakingStyle = userPickedStyle || personality

  const payload: PersonaPayload = {
    name,
    personality,
    speaking_style: speakingStyle
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
    ['user_freeform', answers.user_freeform, MAX_USER_TEXT]
  ]

  for (const [key, raw, max] of optional) {
    const trimmed = truncate(raw, max)

    if (trimmed) {
      payload[key] = trimmed
    }
  }

  return payload
}

// Character-only subset of assemblePersona — strips user_* so enterHatching can finalize before q-user / voice are collected.
export function assembleCharacterPersona(answers: OnboardingAnswers): PersonaPayload {
  const payload = assemblePersona(answers)

  delete payload.user_call_name
  delete payload.user_gender
  delete payload.user_age_bucket
  delete payload.user_hobbies
  delete payload.user_freeform

  return payload
}
