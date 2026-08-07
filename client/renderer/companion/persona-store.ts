import { atom } from 'nanostores'

export interface PersonaDefinition {
  name: string
  personality: string
  speakingStyle: string
  background?: string
  biological_type?: string
  gender?: string
  appearance?: string
}

export const $persona = atom<PersonaDefinition | null>(null)

export async function hydratePersona(): Promise<void> {
  try {
    // All structured persona fields live INSIDE definition_json (a JSON
    // string blob), not as flat top-level keys on the wire.
    const p = await window.deskagent.api<{ definition_json?: string; is_complete?: boolean }>({
      path: '/api/companion/persona'
    })

    if (!p?.is_complete) {
      $persona.set(null)

      return
    }

    let parsed: Record<string, string> = {}

    try {
      const out = p.definition_json ? JSON.parse(p.definition_json) : null

      parsed = typeof out === 'object' && out !== null ? (out as Record<string, string>) : {}
    } catch {
      parsed = {}
    }

    $persona.set({
      name: parsed.name ?? '伙伴',
      personality: parsed.personality ?? '',
      speakingStyle: parsed.speaking_style ?? '',
      background: parsed.background,
      biological_type: parsed.biological_type,
      gender: parsed.gender,
      appearance: parsed.appearance
    })
  } catch {
    $persona.set(null)
  }
}

export type ReactionTone = 'gentle' | 'lively' | 'snarky' | 'calm'

export function personaTone(): ReactionTone {
  const p = ($persona.get()?.personality ?? '').toLowerCase()

  if (p.includes('毒舌') || p.includes('傲娇')) {
    return 'snarky'
  }

  if (p.includes('活泼') || p.includes('好动')) {
    return 'lively'
  }

  if (p.includes('冷静') || p.includes('理性')) {
    return 'calm'
  }

  return 'gentle'
}
