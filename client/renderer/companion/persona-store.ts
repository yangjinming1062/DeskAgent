import { atom } from 'nanostores'

import { safeJsonParse } from '@/shared/lib/safe-json'

import { classifyPersonality } from './persona-classify'

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

export async function hydratePersona(opts: { silent?: boolean } = {}): Promise<{ ok: boolean; error?: unknown }> {
  try {
    // All structured persona fields live INSIDE definition_json (a JSON
    // string blob), not as flat top-level keys on the wire.
    const p = await window.deskagent.api<{ definition_json?: string; is_complete?: boolean }>({
      path: '/api/companion/persona'
    })

    if (!p?.is_complete) {
      // Not-yet-set persona is a valid state, not an error: leave $persona
      // as-is (don't null it out) so a "save just succeeded, hydrate
      // landed and saw stale is_complete" race doesn't look like a wipe
      // to consumers gated on $persona.
      return { ok: true }
    }

    const parsed = safeJsonParse<Record<string, string>>(p.definition_json, {})

    $persona.set({
      name: parsed.name ?? '伙伴',
      personality: parsed.personality ?? '',
      speakingStyle: parsed.speaking_style ?? '',
      background: parsed.background,
      biological_type: parsed.biological_type,
      gender: parsed.gender,
      appearance: parsed.appearance
    })

    return { ok: true }
  } catch (err) {
    // C2: when the caller just successfully PUT a new persona, a transient
    // GET failure here doesn't mean the save failed — the backend has the
    // data. Pass `silent: true` to leave $persona untouched so the user
    // isn't shown a "保存失败" hint + a settings page that hides the
    // 编辑按钮 because $persona became null. The caller's notifier
    // surfaces the GET failure as a soft hint instead.
    if (!opts.silent) {
      $persona.set(null)
    }

    return { ok: false, error: err }
  }
}

export type ReactionTone = 'gentle' | 'lively' | 'snarky' | 'calm'

export function personaTone(): ReactionTone {
  const persona = $persona.get()

  return classifyPersonality(persona?.personality, persona?.background)
}
