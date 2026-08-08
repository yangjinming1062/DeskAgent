import { useCallback, useState } from 'react'

import { type PickedImage } from '@/companion/avatar-image'
import { awaitAvatarRegeneration } from '@/companion/avatar-regen-store'
import { useGatewayRequest } from '@/companion/boot/use-gateway-request'
import { $regenFeedback, applyPortrait, clearRegenFeedback } from '@/companion/portrait-store'

import { playOnboardingAudio } from './onboarding/onboarding-audio'

const DEFAULT_SUCCESS_HINT = '换好啦，新形象已生成～'
const DEFAULT_FAILURE_HINT = '暂时换不出来，稍后再试吧'

export interface UseRegeneratePortraitOptions {
  /**
   * Take the refImage branch (POST /avatar/from-image) instead of the
   * avatar.regenerate RPC. Empty string clears any prior reference image.
   */
  refImage?: PickedImage | null
  /**
   * Play onboarding.portrait.regenerate on success. Off by default so
   * non-onboarding surfaces don't grow audio behaviour they didn't ask for.
   */
  playAudioOnSuccess?: boolean
  /** Override success copy. */
  successHint?: string
  /** Override failure copy. */
  failureHint?: string
}

export interface UseRegeneratePortraitResult {
  /**
   * Per-call feedback overrides options.feedback and the shared
   * $regenFeedback atom. Trimmed; empty becomes undefined. The atom is
   * cleared on every successful regenerate regardless of which source the
   * feedback came from.
   */
  regenerate: (feedback?: string) => Promise<void>
  busy: boolean
  hint: string | null
  clearHint: () => void
}

/**
 * Shared regenerate-the-portrait flow for onboarding, 伙伴设置 → 形象,
 * 重新对话微调性格, and PersonaSection inline editing. Owns the
 * sync/queued split, busy flag, hint copy, and audio cue; the caller
 * supplies a textarea bound to $regenFeedback (via setRegenFeedback /
 * useStore) or passes feedback per-call.
 */
export function useRegeneratePortrait(options: UseRegeneratePortraitOptions = {}): UseRegeneratePortraitResult {
  const { requestGateway } = useGatewayRequest()
  const [busy, setBusy] = useState(false)
  const [hint, setHint] = useState<string | null>(null)

  const {
    refImage,
    playAudioOnSuccess = false,
    successHint = DEFAULT_SUCCESS_HINT,
    failureHint = DEFAULT_FAILURE_HINT
  } = options

  const regenerate = useCallback(
    async (callFeedback?: string) => {
      const fromCall = callFeedback?.trim() || undefined
      const fromOptions = (options as { feedback?: string }).feedback?.trim() || undefined
      const fromAtom = $regenFeedback.get().trim() || undefined
      const feedback = fromCall ?? fromOptions ?? fromAtom

      setBusy(true)
      setHint(null)

      const onApplied = () => {
        clearRegenFeedback()

        if (playAudioOnSuccess) {
          void playOnboardingAudio('onboarding.portrait.regenerate')
        }
      }

      try {
        if (refImage) {
          const res = await window.deskagent.api<{ asset_url?: string; seed_url?: string }>({
            path: '/api/companion/avatar/from-image',
            method: 'POST',
            body: {
              content_type: refImage.contentType,
              image: refImage.base64,
              description: feedback
            }
          })

          if (res?.asset_url) {
            await applyPortrait({ assetUrl: res.asset_url, seedUrl: res.seed_url })
            onApplied()
            setHint(successHint)

            return
          }
        }

        const queued = await requestGateway<{
          queued?: boolean
          job_id?: string
          asset_url?: string
          seed_url?: string
        }>('avatar.regenerate', { feedback })

        if (queued?.asset_url) {
          await applyPortrait({ assetUrl: queued.asset_url, seedUrl: queued.seed_url })
          onApplied()
          setHint(successHint)
        } else if (queued?.queued && queued.job_id) {
          const result = await awaitAvatarRegeneration(queued.job_id)

          if (result.asset_url) {
            await applyPortrait({ assetUrl: result.asset_url, seedUrl: result.seed_url })
            onApplied()
            setHint(successHint)
          } else {
            setHint(result.error ?? failureHint)
          }
        } else {
          setHint(failureHint)
        }
      } catch {
        // The form hint is hidden behind the portrait panel, so duplicate to
        // panel hint; same pattern as onboarding's regeneratePortrait had.
        setHint(failureHint)
      } finally {
        setBusy(false)
      }
    },
    [requestGateway, refImage, playAudioOnSuccess, successHint, failureHint, options]
  )

  const clearHint = useCallback(() => setHint(null), [])

  return { regenerate, busy, hint, clearHint }
}
