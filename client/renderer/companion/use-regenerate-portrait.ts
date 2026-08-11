import { useCallback, useState } from 'react'

import { type PickedImage } from '@/companion/avatar-image'
import { awaitAvatarRegeneration } from '@/companion/avatar-regen-store'
import { useGatewayRequest } from '@/companion/boot/use-gateway-request'
import {
  $activeAvatarId,
  $portraitUrl,
  $regenFeedback,
  $seedUrls,
  applyPortrait,
  clearRegenFeedback,
  pushPortraitEntry,
  type SeedUrls
} from '@/companion/portrait-store'

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
   * Optional presentation/style reference sent alongside the identity anchor
   * as ``presentation_image``. Only consumed by multi-reference providers.
   * When ``refImage`` is absent, this acts as the sole reference (primary
   * ``image``) rather than a secondary.
   */
  presentationRef?: PickedImage | null
  /**
   * Play onboarding.portrait.regenerate on success. Off by default so
   * non-onboarding surfaces don't grow audio behaviour they didn't ask for.
   */
  playAudioOnSuccess?: boolean
  /** Override success copy. */
  successHint?: string
  /** Override failure copy. */
  failureHint?: string
  /**
   * Optional per-call feedback passed via `regenerate(feedback)`. When set,
   * the hook's own copy doesn't shadow the per-call value — callFeedback
   * wins.
   */
  feedback?: string
  /**
   * Fired with the freshly-resolved data URLs after each successful regen.
   * Surfaces that mirror the global `$portraitUrl` into their own local
   * state (e.g. onboarding's paired preview) wire this up to mirror the
   * atom update; surfaces already subscribed via `useStore($portraitUrl)`
   * can omit it.
   */
  onRegenerated?: (urls: { avatar: string | null; seeds: SeedUrls | null; id: number | null }) => void
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
    presentationRef,
    playAudioOnSuccess = false,
    successHint,
    failureHint,
    feedback: optionFeedback,
    onRegenerated
  } = options

  const regenerate = useCallback(
    async (callFeedback?: string) => {
      const fromCall = callFeedback?.trim() || undefined
      const fromOptions = optionFeedback?.trim() || undefined
      const fromAtom = $regenFeedback.get().trim() || undefined
      const feedback = fromCall ?? fromOptions ?? fromAtom

      const resolvedSuccess = successHint ?? DEFAULT_SUCCESS_HINT
      const resolvedFailure = failureHint ?? DEFAULT_FAILURE_HINT

      setBusy(true)
      setHint(null)

      const onApplied = () => {
        pushPortraitEntry({
          portraitUrl: $portraitUrl.get(),
          avatarId: $activeAvatarId.get(),
          seedUrls: $seedUrls.get()
        })
        clearRegenFeedback()

        if (playAudioOnSuccess) {
          void playOnboardingAudio('onboarding.portrait.regenerate')
        }
      }

      try {
        // Q4 image is the identity anchor; presentationRef is a style/presentation
        // hint. When no Q4 image exists, the presentation ref becomes the sole
        // reference (primary image) instead of a secondary.
        const primaryRef = refImage ?? presentationRef
        const secondaryRef = refImage ? presentationRef : null

        if (primaryRef) {
          const res = await window.deskagent.api<{
            asset_url?: string | null
            seed_front_url?: string | null
            seed_right_url?: string | null
            seed_back_url?: string | null
            id?: number
          }>({
            path: '/api/companion/avatar/from-image',
            method: 'POST',
            body: {
              content_type: primaryRef.contentType,
              image: primaryRef.base64,
              description: feedback,
              ...(secondaryRef && {
                presentation_image: secondaryRef.base64,
                presentation_content_type: secondaryRef.contentType
              })
            }
          })

          if (res?.asset_url) {
            const applied = await applyPortrait({
              id: res.id,
              assetUrl: res.asset_url,
              seedFrontUrl: res.seed_front_url,
              seedRightUrl: res.seed_right_url,
              seedBackUrl: res.seed_back_url
            })

            onRegenerated?.({ ...applied, id: res.id ?? null })
            onApplied()
            setHint(resolvedSuccess)

            return
          }
        }

        const queued = await requestGateway<{
          queued?: boolean
          job_id?: string
          asset_url?: string | null
          seed_front_url?: string | null
          seed_right_url?: string | null
          seed_back_url?: string | null
          id?: number
        }>('avatar.regenerate', { feedback })

        const settled =
          queued && 'asset_url' in queued
            ? queued
            : queued?.queued && queued.job_id
              ? await awaitAvatarRegeneration(queued.job_id)
              : null

        if (settled?.asset_url) {
          const applied = await applyPortrait({
            id: settled.id,
            assetUrl: settled.asset_url,
            seedFrontUrl: settled.seed_front_url,
            seedRightUrl: settled.seed_right_url,
            seedBackUrl: settled.seed_back_url
          })

          onRegenerated?.({ ...applied, id: settled.id ?? null })
          onApplied()
          setHint(resolvedSuccess)
        } else {
          setHint(settled && 'error' in settled ? (settled.error ?? resolvedFailure) : resolvedFailure)
        }
      } catch {
        setHint(resolvedFailure)
      } finally {
        setBusy(false)
      }
    },
    // Depend on the destructured primitives, not on the `options` object
    // itself — callers pass a fresh literal each render, which would
    // otherwise give `regenerate` a new identity every render and defeat
    // downstream React.memo. optionFeedback participates so a caller can
    // change it without remounting the hook.
    [
      requestGateway,
      refImage,
      presentationRef,
      playAudioOnSuccess,
      successHint,
      failureHint,
      optionFeedback,
      onRegenerated
    ]
  )

  const clearHint = useCallback(() => setHint(null), [])

  return { regenerate, busy, hint, clearHint }
}
