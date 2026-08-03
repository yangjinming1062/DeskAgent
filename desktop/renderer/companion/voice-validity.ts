// Detects when the persisted companion voice id ($companionVoiceId) is no
// longer present in the active provider's cloud catalog — e.g. the provider
// pruned/renamed voices, or the user switched providers. The backend's
// pick_voice_id tolerates unknown ids (falls back to the provider default),
// so synthesis never breaks; this is a prompt, not a hard error.
//
// Design tokens ("mimo_voicedesign:<prompt>") and the empty default are
// always considered valid. P2-8 (runtime audit): distinguish "fetch
// failed" (transient — keep current voice, no prompt) from
// "fetch succeeded but voice not in catalog" (real miss — prompt the
// user to re-pick). The previous code collapsed both into
// {valid: true} so a user who switched providers never saw the
// "your voice changed underneath you" hint.

import type { RequestGateway } from '@/shared/voice-catalog'
import { VOICEDESIGN_PREFIX } from '@/shared/voice-catalog'

import { $companionVoiceId } from './prefs'
import { fetchVoiceCatalogRaw } from './voice'

export type VoiceValidityResult =
  | { valid: true }
  | { valid: false; name: string; reason: 'fetch_failed' | 'catalog_miss' }

export async function checkCompanionVoiceValidity(requestGateway: RequestGateway): Promise<VoiceValidityResult> {
  const id = $companionVoiceId.get()

  if (!id || id.startsWith(VOICEDESIGN_PREFIX)) {
    return { valid: true }
  }

  const result = await fetchVoiceCatalogRaw(requestGateway)

  if (!result.ok) {
    // Transient: don't prompt the user — the next connectivity
    // cycle will re-evaluate.
    return { valid: true }
  }

  return result.catalog.voices.some(v => v.id === id)
    ? { valid: true }
    : { valid: false, name: id, reason: 'catalog_miss' }
}
