/** Puppet 生产数据源 — 从 mesh2d 行的 manifest 分流 PSD 链。
 *
 * 后端 see-through 产出 `spiritagent.2d.psd/1` 描述符（kind=psd）复用 mesh2d 行 /
 * 事件管线；本 store 只做一件事：拉 manifest 判 kind——psd 则暴露签名 PSD URL
 * 供 PuppetStage 装配，否则保持未就绪让渲染级联落到 3D/蛋兜底（DESIGN §1.2）。
 * 后端只产 psd 一种 manifest，非 psd 分支仅作防御性清空。
 */

import { computed } from 'nanostores'

import { $mesh2dInfo } from '@/2d/mesh2d/mesh2d-store'
import { authedApi } from '@/shared/lib/authed-api'
import { log } from '@/shared/lib/log'
import { definePersistedAtom } from '@/shared/lib/storage'
import { $auth } from '@/shared/store/auth'

interface PuppetInfo {
  psdUrl: string | null
  contentHash: string | null
  error: string | null
}

const DEFAULT_PUPPET_INFO: PuppetInfo = { contentHash: null, error: null, psdUrl: null }

function isPersistablePuppet(val: unknown): val is PuppetInfo {
  if (typeof val !== 'object' || val === null) {
    return false
  }

  const v = val as Partial<PuppetInfo>

  return typeof v.psdUrl === 'string' && Boolean(v.psdUrl) && !v.error
}

const puppetInfoPersisted = definePersistedAtom<PuppetInfo>({
  fallback: DEFAULT_PUPPET_INFO,
  isPersistable: isPersistablePuppet,
  key: 'da.companion.puppet'
})

export const $puppetInfo = puppetInfoPersisted.$atom
export const resetPuppet = puppetInfoPersisted.reset

// 渲染级联的 puppet 门闩：由 $puppetInfo 派生（PSD URL 已知且未出错才点亮）
export const $puppetReady = computed($puppetInfo, info => Boolean(info.psdUrl) && !info.error)

// 缓存「manifest 是否 PSD 描述符」：contentHash 优先，缺时退回 manifestUrl
// （5 分钟轮换，但同 URL 在会话内高频复用）。容量 256 项防内存泄漏。
const KIND_CACHE = new Map<string, boolean>()

function setKindCache(key: string, isPsd: boolean): void {
  if (KIND_CACHE.size >= 256) {
    const firstKey = KIND_CACHE.keys().next().value

    if (firstKey) {
      KIND_CACHE.delete(firstKey)
    }
  }

  KIND_CACHE.set(key, isPsd)
}

export function setPuppetError(error: string): void {
  // 必须同步清掉 localStorage：signature URL 5 分钟过期，cold start 读出过期的
  // psdUrl 会让 PuppetStage 立刻 fetch 失败，循环触发 error。reset() 复用
  // puppetInfoPersisted 自身的 persist 钩子，保证内存 + 磁盘同步。
  puppetInfoPersisted.reset()
  $puppetInfo.set({ contentHash: null, error, psdUrl: null })
}

/** 读取当前 2D 行的 manifest 判定 psd 链；在 hydrateMesh2D 完成后调用。 */
export async function hydratePuppet(): Promise<void> {
  const info = $mesh2dInfo.get()

  if (info.status !== 'succeeded' || !info.manifestUrl) {
    resetPuppet()

    return
  }

  // 缓存键 fallback：contentHash 缺失时退回 manifestUrl（5 分钟轮换），
  // 让 KIND_CACHE 仍能抑制同 URL 反复 manifest 拉取的浪费。
  const cacheKey = info.contentHash ?? info.manifestUrl
  const cached = KIND_CACHE.get(cacheKey)

  if (cached === false) {
    // 已知是 mesh2d 描述符：清空 puppet 状态即可，不重复拉 manifest。
    resetPuppet()

    return
  }

  let manifest: { kind?: string; schema?: string } | null = null

  if (typeof window !== 'undefined' && typeof window.spiritagent?.api === 'function') {
    const result = await authedApi<{ kind?: string; schema?: string }>({ path: info.manifestUrl })

    if (!result.ok) {
      if (result.reason === 'err') {
        log.warn('puppet-store', 'hydratePuppet manifest fetch failed; falling back', result.error)
      }

      puppetInfoPersisted.set({ contentHash: null, error: null, psdUrl: null })

      return
    }

    manifest = result.value
  } else {
    try {
      // eslint-disable-next-line no-restricted-syntax -- 无桥防御分支：桥在时永不执行，避免为死分支引第二条依赖
      const res = await fetch(info.manifestUrl, { credentials: 'include' })

      if (!res.ok) {
        throw new Error(`manifest fetch failed: ${res.status}`)
      }

      manifest = (await res.json()) as { kind?: string; schema?: string }
    } catch (err) {
      log.warn('puppet-store', 'hydratePuppet failed; falling back', err)
      puppetInfoPersisted.set({
        contentHash: null,
        error: err instanceof Error ? err.message : String(err),
        psdUrl: null
      })

      return
    }
  }

  if ($auth.get().kind !== 'authenticated') {
    return
  }

  const isPsd = manifest?.kind === 'psd' && (manifest.schema ?? '').startsWith('spiritagent.2d.psd')

  setKindCache(cacheKey, isPsd)

  if (!isPsd) {
    resetPuppet()

    return
  }

  const psdUrl = info.layerUrls['psd'] ?? null

  if (!psdUrl) {
    log.warn('puppet-store', 'psd manifest missing layer_urls.psd; falling back')
    resetPuppet()

    return
  }

  puppetInfoPersisted.set({ contentHash: info.contentHash, error: null, psdUrl })
  log.info('puppet-store', 'psd manifest detected')
}
