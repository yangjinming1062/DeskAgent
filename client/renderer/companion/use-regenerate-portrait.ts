import { useCallback, useState } from 'react'

import { type PickedImage } from '@/companion/avatar-image'
import { awaitAvatarRegeneration } from '@/companion/avatar-regen-store'
import { useGatewayRequest } from '@/companion/boot/use-gateway-request'
import {
  $activeAvatarId,
  $portraitUrl,
  $regenFeedback,
  applyPortrait,
  pushPortraitEntry
} from '@/companion/portrait-store'

import { playOnboardingAudio } from './onboarding/onboarding-audio'

const DEFAULT_SUCCESS_HINT = '换好啦，新形象已生成～'
const DEFAULT_FAILURE_HINT = '暂时换不出来，稍后再试吧'

interface UseRegeneratePortraitOptions {
  /**
   * 走 refImage 分支（POST /avatar/from-image），而不是 avatar.regenerate RPC。
   * 空字符串会清除之前的参考图。
   */
  refImage?: PickedImage | null
  /**
   * 与身份锚一起以 ``presentation_image`` 发送的可选表现/风格参考。
   * 仅多参考供应商会消费它。当 ``refImage`` 不存在时，本字段充当唯一参考
   * （主 ``image``）而非辅图。
   */
  presentationRef?: PickedImage | null
  /**
   * 成功时播放 onboarding.portrait.regenerate。
   * 默认关闭，避免非 onboarding 页面意外加上没有申请的音效行为。
   */
  playAudioOnSuccess?: boolean
  /** 覆盖成功提示文案。 */
  successHint?: string
  /** 覆盖失败提示文案。 */
  failureHint?: string
  /**
   * 通过 `regenerate(feedback)` 传入的可选逐次反馈。设置后，
   * hook 自身的文案不会覆盖本次调用的值——callFeedback 优先。
   */
  feedback?: string
  /**
   * 每次成功重生成后用刚解析到的 data URL 触发。把全局 `$portraitUrl` 同步到
   * 自己本地状态的页面（如 onboarding 的成对预览）需要接入它来镜像 atom 更新；
   * 已通过 `useStore($portraitUrl)` 订阅的页面可以省略。
   */
  onRegenerated?: (urls: { avatar: string | null; id: number | null }) => void
}

interface UseRegeneratePortraitResult {
  /**
   * 逐次反馈优先于 options.feedback 和共享的 $regenFeedback atom。
   * 会 trim；空串转为 undefined。无论反馈来自哪条路径，
   * 每次成功 regenerate 后都会清空 atom。
   *
   * overrideRef：逐次身份参考覆盖（DESIGN §5.4「自己上传」上传即重绘——
   * 调用点刚写入新 refImage 时 hook 闭包还持有旧值，只能经参数传新图）。
   */
  regenerate: (feedback?: string, overrideRef?: PickedImage | null) => Promise<void>
  busy: boolean
  hint: string | null
  clearHint: () => void
}

/**
 * 跨 onboarding、伙伴设置 → 形象、重新对话微调性格、PersonaSection 内联编辑
 * 共用的重生成形象流程。负责同步/排队分流、busy 标记、提示文案、音效提示；
 * 调用方提供一个绑定到 $regenFeedback 的 textarea（通过 $regenFeedback.set /
 * useStore）或逐次传入 feedback。
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
    async (callFeedback?: string, overrideRef?: PickedImage | null) => {
      const fromCall = callFeedback?.trim() || undefined
      const fromOptions = optionFeedback?.trim() || undefined
      const fromAtom = $regenFeedback.get().trim() || undefined
      const feedback = fromCall ?? fromOptions ?? fromAtom
      const effRefImage = overrideRef !== undefined ? overrideRef : refImage

      const resolvedSuccess = successHint ?? DEFAULT_SUCCESS_HINT
      const resolvedFailure = failureHint ?? DEFAULT_FAILURE_HINT

      setBusy(true)
      setHint(null)

      const onApplied = () => {
        pushPortraitEntry({
          portraitUrl: $portraitUrl.get(),
          avatarId: $activeAvatarId.get()
        })
        $regenFeedback.set('')

        if (playAudioOnSuccess) {
          void playOnboardingAudio('onboarding.portrait.regenerate')
        }
      }

      try {
        // Q4 图是身份锚；presentationRef 是风格/表现提示。
        // 没有 Q4 图时，presentation ref 变成唯一的参考图（主图）而非辅图。
        const primaryRef = effRefImage ?? presentationRef
        const secondaryRef = effRefImage ? presentationRef : null

        if (primaryRef) {
          const res = await window.spiritagent.api<{
            asset_url?: string | null
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
              assetUrl: res.asset_url
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
            assetUrl: settled.asset_url
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
    // 依赖项用解构出来的基本值，而非 `options` 对象本身——调用方每次渲染
    // 都会传入新字面量，否则会让 `regenerate` 每次渲染都获得新身份，
    // 抵消下游 React.memo 的效果。optionFeedback 参与依赖是为了让调用方
    // 在不重新挂载 hook 的情况下更改它。
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
