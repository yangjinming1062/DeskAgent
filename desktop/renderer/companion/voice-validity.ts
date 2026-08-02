// Detects when the persisted companion voice id ($companionVoiceId) is no
// longer present in the active provider's cloud catalog — e.g. the provider
// pruned/renamed voices, or the user switched providers. The backend's
// pick_voice_id tolerates unknown ids (falls back to the provider default),
// so synthesis never breaks; this is a prompt, not a hard error.
//
// Design tokens ("mimo_voicedesign:<prompt>") and the empty default are
// always considered valid. A catalog fetch failure is treated as valid to
// avoid false positives on transient gateway/network hiccups.

import { $companionVoiceId } from './prefs'
import { fetchVoiceCatalog } from './voice'

type RequestGateway = <T>(method: string, params?: Record<string, unknown>) => Promise<T>

export interface VoiceValidityResult {
  valid: boolean
  /** Best-effort label for the invalid id (the id itself when unknown). */
  name?: string
}

export async function checkCompanionVoiceValidity(requestGateway: RequestGateway): Promise<VoiceValidityResult> {
  const id = $companionVoiceId.get()

  if (!id || id.includes(':')) {
    return { valid: true }
  }

  try {
    const catalog = await fetchVoiceCatalog(requestGateway)
    const match = catalog.voices.find(v => v.id === id)

    if (match) {
      return { valid: true }
    }

    return { valid: false, name: id }
  } catch {
    return { valid: true }
  }
}
