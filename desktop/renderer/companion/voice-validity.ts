import type { RequestGateway } from '@/shared/voice-catalog'
import { VOICEDESIGN_PREFIX } from '@/shared/voice-catalog'

import { $companionVoiceId } from './prefs'
import { fetchVoiceCatalogRaw } from './voice'

export type VoiceValidityResult = { valid: true } | { valid: false; name: string; reason: 'catalog_miss' }

export async function checkCompanionVoiceValidity(requestGateway: RequestGateway): Promise<VoiceValidityResult> {
  const id = $companionVoiceId.get()

  if (!id || id.startsWith(VOICEDESIGN_PREFIX)) {
    return { valid: true }
  }

  const result = await fetchVoiceCatalogRaw(requestGateway)

  if (!result.ok) {
    return { valid: true }
  }

  return result.catalog.voices.some(v => v.id === id)
    ? { valid: true }
    : (() => {
        // Clear the stale id so the next speak() omits the voice arg
        $companionVoiceId.set('')

        return { valid: false, name: id, reason: 'catalog_miss' }
      })()
}
