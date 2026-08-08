import { isLatestGen, nextGen, playDataUrl } from '../audio-track'
import { $voicePreparing } from '../voice-state'

// Pre-rendered cloud-TTS clips for the onboarding flow. Tag list mirrors
// installer/payload/onboarding-audio/manifest.json; each tag corresponds to
// exactly one mp3 under $DESKAGENT_HOME/audio/onboarding/zh/.
export type OnboardingAudioTag =
  | `onboarding.q${number}`
  | 'onboarding.hatching'
  | 'onboarding.hatching.retry'
  | 'onboarding.portrait.ok'
  | 'onboarding.portrait.failed'
  | 'onboarding.portrait.regenerate'
  | 'onboarding.finishing.retry'
  | 'onboarding.greeting'

export async function playOnboardingAudio(tag: OnboardingAudioTag): Promise<boolean> {
  const gen = nextGen()
  $voicePreparing.set(true)

  try {
    const res = await window.deskagent.media.onboardingAudio.read(tag)

    if (!isLatestGen(gen)) {
      return false
    }

    return await playDataUrl(res.dataUrl)
  } catch (error) {
    // Pre-rendered clip is the source of truth for onboarding voice — never
    // silently fall back to runtime TTS. Surface the missing file loudly so
    // dev/QA catches a broken installer payload on the spot.
    console.error('[onboarding-audio] missing pre-rendered clip', tag, error)

    return false
  } finally {
    if (isLatestGen(gen)) {
      $voicePreparing.set(false)
    }
  }
}
