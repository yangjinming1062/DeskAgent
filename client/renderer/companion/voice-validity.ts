import type { RequestGateway } from '@/shared/voice-catalog'
import { VOICEDESIGN_PREFIX } from '@/shared/voice-catalog'

import { $companionVoiceId } from './prefs'
import { fetchVoiceCatalogRaw } from './voice'

type VoiceValidityResult = { valid: true } | { valid: false; name: string; reason: 'catalog_miss' }

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
        // 清除过期的 id，使下次 speak() 不再带 voice 参数
        $companionVoiceId.set('')

        return { valid: false, name: id, reason: 'catalog_miss' }
      })()
}
