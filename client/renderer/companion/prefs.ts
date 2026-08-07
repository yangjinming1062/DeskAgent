import { atom } from 'nanostores'

import { persistString, storedString } from '@/shared/lib/storage'

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
