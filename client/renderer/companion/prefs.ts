import { atom, type WritableAtom } from 'nanostores'

import { persistBoolean, persistString, storedBoolean, storedString } from '@/shared/lib/storage'

// Response mode controls how the companion replies in Chat mode (plan §4.1).
// Voice-call mode is always voice regardless of this setting.
export type ResponseMode = 'text' | 'voice'

export const $companionVoiceId = atom<string>(storedString('da.companion.voiceId') ?? '')
export const $responseMode = atom<ResponseMode>((storedString('da.companion.responseMode') as ResponseMode) ?? 'text')

export function setCompanionVoiceId(voice: string): void {
  $companionVoiceId.set(voice)
  persistString('da.companion.voiceId', voice || null)
}

export function setResponseMode(mode: ResponseMode): void {
  $responseMode.set(mode)
  persistString('da.companion.responseMode', mode)
}

// Boolean toggles backed by localStorage — used for the LLM-driven
// reaction switches in companion settings. Each toggle's persistence
// is created atomically by the factory so a future addition only
// needs one line.
export interface BooleanPref {
  $atom: WritableAtom<boolean>
  set: (value: boolean) => void
}

export function makeBooleanPref(key: string, fallback: boolean): BooleanPref {
  const $atom = atom<boolean>(storedBoolean(key, fallback))

  return {
    $atom,
    set(value: boolean): void {
      $atom.set(value)
      persistBoolean(key, value)
    }
  }
}

export const llmReactionsPref = makeBooleanPref('da.companion.llmReactions', true)
export const llmAffectPref = makeBooleanPref('da.companion.llmAffect', true)
export const llmAutonomyPref = makeBooleanPref('da.companion.llmAutonomy', true)

export const $llmReactions = llmReactionsPref.$atom
export const $llmAffect = llmAffectPref.$atom
export const $llmAutonomy = llmAutonomyPref.$atom

export const setLlmReactions = llmReactionsPref.set
export const setLlmAffect = llmAffectPref.set
export const setLlmAutonomy = llmAutonomyPref.set
