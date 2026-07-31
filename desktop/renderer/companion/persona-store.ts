import { atom } from 'nanostores'

// Hydrated persona definition (GET /api/companion/persona). Drives persona-
// aware behaviour like interaction-reaction tone (plan §4.3). Empty until the
// companion boots; consumers degrade gracefully to a neutral default.
export interface PersonaDefinition {
  name: string
  personality: string
  speakingStyle: string
  background?: string
}

export const $persona = atom<PersonaDefinition | null>(null)

export async function hydratePersona(): Promise<void> {
  try {
    const p = await window.deskagent.api<{ definition_json?: string; is_complete?: boolean; name?: string; personality?: string; speaking_style?: string; background?: string }>({ path: '/api/companion/persona' })

    if (!p?.is_complete) {
      $persona.set(null)

      return
    }

    // Persona may be persisted as definition_json or flat fields.
    let parsed: Record<string, string> = {}

    try {
      parsed = p.definition_json ? JSON.parse(p.definition_json) : {}
    } catch {
      parsed = {}
    }

    $persona.set({
      name: p.name ?? parsed.name ?? '伙伴',
      personality: p.personality ?? parsed.personality ?? '',
      speakingStyle: p.speaking_style ?? parsed.speaking_style ?? '',
      background: p.background ?? parsed.background
    })
  } catch {
    $persona.set(null)
  }
}

// Coarse reaction tone derived from personality keywords (plan §4.3 layered
// personalisation — full LLM+memory generation is a future enhancement; this
// gives same-action/different-personality variation today).
export type ReactionTone = 'gentle' | 'lively' | 'snarky' | 'calm'

export function personaTone(): ReactionTone {
  const p = ($persona.get()?.personality ?? '').toLowerCase()

  if (p.includes('毒舌') || p.includes('傲娇')) {return 'snarky'}

  if (p.includes('活泼') || p.includes('好动')) {return 'lively'}

  if (p.includes('冷静') || p.includes('理性')) {return 'calm'}

  return 'gentle'
}
