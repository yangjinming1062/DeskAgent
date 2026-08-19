import { atom, computed } from 'nanostores'

import { isClientErrorIpc } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'

import { $modelInfo } from '../3d/model-store'
import { $companionLifecycle } from '../companion-store'

// 静态精灵相册 store——无 GLB 时的降级渲染器（生成中 / 失败 / 无 key / 加载空挡）。
// 等待图在每次进入静态模式时请求一次；状态与情绪变化映射为自由语义
// （sprite-semantics.ts），由后端做匹配或生成。失败时保留当前图像——绝不空白。

export interface ActiveSprite {
  dataUrl: string
  tag: string
}

export const $activeSprite = atom<ActiveSprite | null>(null)
// 当 CharacterController.load 回退到程序化蛋兜底时为 true——
// 由 companion-3d 的加载 effect 设置；在 GLB 真正解析完成前控制
// $hasRenderableModel（model.ready 在字节到达前就已翻为 'succeeded'）。
export const $glbLoadFailed = atom<boolean>(false)

interface SpriteResolveResponse {
  id: number
  url: string
  tag: string
  content_hash?: string | null
  generated: boolean
}

// 小 PNG 走 apiAsset data-URL 通道（与衣橱纹理共用）；
// 按 content_hash 索引，因此重新解析相册中已缓存的行可跳过拉取。
const _urlCache = new Map<string, string>()
const _requestCache = new Map<string, ActiveSprite>()
const _inflight = new Map<string, Promise<void>>()

// 给不同的 POST 留出间隔：相册由 LLM 支撑，状态变化可能突发
// （戳击 → interacting → 上一状态快速切换）。trailing 定时器保证窗口期过后
// 最新状态请求会被执行。
const MIN_REQUEST_SPACING_MS = 1500
let _lastPostAt = 0
let _pendingTimer: ReturnType<typeof setTimeout> | null = null
let _latestPending: { request: string; role?: 'waiting' } | null = null

/** 把语义请求发到后端相册；成功时发布到 $activeSprite，
 * 失败时静默保留当前图像。 */
export async function requestSprite(request: string, role?: 'waiting'): Promise<void> {
  const cached = _requestCache.get(request)

  if (cached) {
    if (_latestPending?.request === request) {
      _latestPending = null
    }

    $activeSprite.set(cached)

    return
  }

  const inflight = _inflight.get(request)

  if (inflight) {
    await inflight
    const resolved = _requestCache.get(request)

    if (resolved) {
      $activeSprite.set(resolved)
    }

    return
  }

  if (Date.now() - _lastPostAt < MIN_REQUEST_SPACING_MS) {
    _latestPending = { request, role }

    if (_pendingTimer === null) {
      const delay = Math.max(50, MIN_REQUEST_SPACING_MS - (Date.now() - _lastPostAt))
      _pendingTimer = setTimeout(() => {
        _pendingTimer = null

        if (_latestPending) {
          const next = _latestPending
          _latestPending = null
          void requestSprite(next.request, next.role)
        }
      }, delay)
    }

    return
  }

  if (_latestPending?.request === request) {
    _latestPending = null
  }

  const task = (async () => {
    _lastPostAt = Date.now()

    try {
      const res = await window.spiritagent.api<SpriteResolveResponse>({
        path: '/api/companion/sprite',
        method: 'POST',
        body: role ? { request, role } : { request }
      })

      const cacheKey = res.content_hash ?? res.url
      let dataUrl = _urlCache.get(cacheKey)

      if (!dataUrl) {
        dataUrl = await window.spiritagent.apiAsset({ url: res.url })
        _urlCache.set(cacheKey, dataUrl)
      }

      const active: ActiveSprite = { dataUrl, tag: res.tag }
      _requestCache.set(request, active)
      $activeSprite.set(active)
    } catch (error) {
      if (!isClientErrorIpc(error)) {
        log.warn('sprite-store', 'requestSprite failed', error)
      }
    } finally {
      _inflight.delete(request)
    }
  })()

  _inflight.set(request, task)
  await task
}

/** 形象重生成会让相册的身份锚失效（服务端按 avatar_id 过滤）——
 * 清掉本地缓存，让下次请求生成全新的精灵。
 */
export function resetSpriteAlbum(): void {
  if (_pendingTimer !== null) {
    clearTimeout(_pendingTimer)
    _pendingTimer = null
  }

  _latestPending = null
  _urlCache.clear()
  _requestCache.clear()
  _inflight.clear()
  _lastPostAt = 0
  $activeSprite.set(null)
}

export const $hasRenderableModel = computed(
  [$modelInfo, $glbLoadFailed],
  (model, loadFailed) => Boolean(model.asset_url) && model.status === 'succeeded' && !loadFailed
)

export const $staticMode = computed(
  [$companionLifecycle, $hasRenderableModel],
  (lifecycle, hasModel) => lifecycle !== 'unauthed' && !hasModel
)
