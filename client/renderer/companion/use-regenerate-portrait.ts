import { useCallback, useState } from 'react'

import { type PickedImage } from '@/companion/avatar-image'
import { awaitAvatarRegeneration, awaitFullbodyGeneration } from '@/companion/avatar-regen-store'
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
const FULLBODY_SUCCESS_HINT = '全身图已生成～'
const FULLBODY_FAILURE_HINT = '全身图暂时换不出来，稍后再试吧'

export type PortraitStep = 'avatar' | 'fullbody'

export interface UseRegeneratePortraitOptions {
  /**
   * Take the refImage branch (POST /avatar/from-image) instead of the
   * avatar.regenerate RPC. Empty string clears any prior reference image.
   * Only meaningful when ``step === 'avatar'`` — fullbody never uses refImage
   * because the avatar already supplies the visual reference.
   */
  refImage?: PickedImage | null
  /**
   * Which pipeline this call drives. ``'avatar'`` (default) regenerates the
   * bust and returns ``{avatar, seeds}`` with seeds empty/null. ``'fullbody'``
   * re-runs only the seed step on top of the already-confirmed avatar row;
   * pass ``avatarId`` so the backend knows which row to update.
   */
  step?: PortraitStep
  /**
   * Active avatar row id — required when ``step === 'fullbody'`` so the
   * backend can locate the cached seed_prompt + persisted bytes.
   */
  avatarId?: number | null
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
   * wins. Typed explicitly so the unsafe cast at line 65 stays gone.
   */
  feedback?: string
  /**
   * Fired with the freshly-resolved data URLs after each successful regen.
   * Surfaces that mirror the global `$portraitUrl` into their own local
   * state (e.g. onboarding's paired preview) wire this up to mirror the
   * atom update; surfaces already subscribed via `useStore($portraitUrl)`
   * can omit it. ``id`` is the avatar row id so callers driving the two-step
   * flow can keep ``pendingAvatarId`` in sync after a step-1 regen.
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
 *
 * Two-step avatar/fullbody flows (onboarding + settings) call this hook
 * twice with different ``step`` values; the per-call call site owns the
 * state machine so this hook stays a thin wrapper.
 */
export function useRegeneratePortrait(options: UseRegeneratePortraitOptions = {}): UseRegeneratePortraitResult {
  const { requestGateway } = useGatewayRequest()
  const [busy, setBusy] = useState(false)
  const [hint, setHint] = useState<string | null>(null)

  const {
    refImage,
    step = 'avatar',
    avatarId,
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

      const isFullbody = step === 'fullbody'
      const resolvedSuccess = successHint ?? (isFullbody ? FULLBODY_SUCCESS_HINT : DEFAULT_SUCCESS_HINT)
      const resolvedFailure = failureHint ?? (isFullbody ? FULLBODY_FAILURE_HINT : DEFAULT_FAILURE_HINT)

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
        if (isFullbody) {
          if (typeof avatarId !== 'number' || avatarId <= 0) {
            setHint('找不到对应的形象，请重新打开设置')

            return
          }

          const queued = await requestGateway<{
            queued?: boolean
            job_id?: string
            seed_front_url?: string | null
            seed_right_url?: string | null
            seed_back_url?: string | null
            id?: number
            error?: string
          }>('avatar.generate_fullbody', { avatar_id: avatarId })

          const settled =
            queued && 'seed_front_url' in queued
              ? queued
              : queued?.queued && queued.job_id
                ? await awaitFullbodyGeneration(queued.job_id)
                : null

          if (settled?.seed_front_url) {
            const applied = await applyPortrait({
              assetUrl: null,
              seedFrontUrl: settled.seed_front_url,
              seedRightUrl: settled.seed_right_url,
              seedBackUrl: settled.seed_back_url
            })

            onRegenerated?.({ ...applied, id: settled.id ?? null })
            onApplied()
            setHint(resolvedSuccess)
          } else {
            setHint(settled?.error ?? resolvedFailure)
          }

          return
        }

        if (refImage) {
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
              content_type: refImage.contentType,
              image: refImage.base64,
              description: feedback
            }
          })

          if (res?.asset_url) {
            const applied = await applyPortrait({
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
      step,
      avatarId,
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
