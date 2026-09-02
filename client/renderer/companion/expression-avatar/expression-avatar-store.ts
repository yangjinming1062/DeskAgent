import { atom } from 'nanostores'

import { $spriteEmotion } from '@/companion/companion-store'
import { isClientErrorIpc } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'

// 聊天面板中的表情头像——affect 激活时显示在聊天旁的情绪脸。
// 按 tag 查找（后端按情绪 token 匹配或生成）；失败时保留肖像兜底。

export interface ExpressionAvatar {
  name: string
  dataUrl: string
}

export const $expressionAvatar = atom<ExpressionAvatar | null>(null)

// 按情绪 token 解析（请求 key 与服务端缓存行 1:1、token 精确匹配，
// 无 LLM 语义匹配，因此单一缓存即可）。
// 小 PNG 走 apiAsset 的 data-URL 通道，与装扮贴图相同。
const resolvedCache = new Map<string, ExpressionAvatar>()
const inflightMap = new Map<string, Promise<void>>()
const failedAtMap = new Map<string, number>()

const FAILURE_BACKOFF_MS = 60_000
// 由 resetExpressionAvatars 自增——reset 之前启动的任务
// （头像在生成中途被重新生成）不得用旧结果再次写缓存。
let resetEpoch = 0

/** 解析情绪对应的头像；在情绪仍处于活跃状态时写入 $expressionAvatar。
 * neutral / 无意义的情绪不会触发请求。 */
export async function requestExpressionAvatar(name: string): Promise<void> {
  const normalized = name.trim().toLowerCase()

  if (!normalized || normalized === 'neutral') {
    return
  }

  const cached = resolvedCache.get(normalized)

  if (cached) {
    $expressionAvatar.set(cached)

    return
  }

  const lastFailedAt = failedAtMap.get(normalized)

  if (lastFailedAt !== undefined && Date.now() - lastFailedAt < FAILURE_BACKOFF_MS) {
    return
  }

  const pendingTask = inflightMap.get(normalized)

  if (pendingTask) {
    // 拥有者任务在完成后负责发布 / 缓存 / 退避——加入方只等；
    // 在这里重跑那段逻辑会与守卫重复。
    await pendingTask

    return
  }

  const epoch = resetEpoch

  const task = (async () => {
    try {
      const res = await window.spiritagent.api<{ url: string }>({
        path: '/api/companion/expression-avatar',
        method: 'POST',
        body: { name: normalized }
      })

      const dataUrl = await window.spiritagent.apiAsset({ url: res.url })
      const active: ExpressionAvatar = { name: normalized, dataUrl }

      // 慢速生成绝不浪费：结果始终落到缓存（服务端行 + 此处），
      // 下次使用时即时显示。展示区仅在该情绪仍是当前活跃情绪时才切换。
      if (epoch === resetEpoch) {
        resolvedCache.set(normalized, active)

        if ($spriteEmotion.get() === normalized) {
          $expressionAvatar.set(active)
        }
      }
    } catch (error) {
      if (!isClientErrorIpc(error)) {
        log.warn('expression-avatar', 'requestExpressionAvatar failed', error)
      }

      // 当前情绪的肖像兜底——即便展示区仍保留着过期的旧脸。
      if (epoch === resetEpoch) {
        failedAtMap.set(normalized, Date.now())

        if ($spriteEmotion.get() === normalized) {
          $expressionAvatar.set(null)
        }
      }
    } finally {
      inflightMap.delete(normalized)
    }
  })()

  inflightMap.set(normalized, task)
  await task
}

/** 仅清空显示（情绪结束）——缓存保留给下次使用。 */
export function clearExpressionAvatar(): void {
  $expressionAvatar.set(null)
}

/** 头像重新生成会让身份锚点失效（服务端按 avatar_id 给行做键）——
 * 清空本地缓存，使下次请求能解析出全新图像。 */
export function resetExpressionAvatars(): void {
  resetEpoch++
  resolvedCache.clear()
  inflightMap.clear()
  failedAtMap.clear()
  $expressionAvatar.set(null)
}
