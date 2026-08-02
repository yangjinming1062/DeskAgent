// Detects when the persisted companion voice id ($companionVoiceId) is no
// longer present in the active provider's cloud catalog — e.g. the provider
// pruned/renamed voices, or the user switched providers. The backend's
// pick_voice_id tolerates unknown ids (falls back to the provider default),
// so synthesis never breaks; this is a prompt, not a hard error.
//
// Design tokens ("mimo_voicedesign:<prompt>") and the empty default are
// always considered valid. A catalog fetch failure is treated as valid to
// avoid false positives on transient gateway/network hiccups.

import type { RequestGateway } from '@/shared/voice-catalog'
import { VOICEDESIGN_PREFIX } from '@/shared/voice-catalog'

import { $companionVoiceId } from './prefs'
import { fetchVoiceCatalog } from './voice'

export type VoiceValidityResult = { valid: true } | { valid: false; name: string }

export async function checkCompanionVoiceValidity(requestGateway: RequestGateway): Promise<VoiceValidityResult> {
  const id = $companionVoiceId.get()

  if (!id || id.startsWith(VOICEDESIGN_PREFIX)) {
    return { valid: true }
  }

  try {
    const catalog = await fetchVoiceCatalog(requestGateway)
    const match = catalog.voices.find(v => v.id === id)

    return match ? { valid: true } : { valid: false, name: id }
  } catch {
    return { valid: true }
  }
}