/** Puppet 生产数据源 — 从 mesh2d 行的 manifest 分流 PSD 链（Phase 6）。
 *
 * 后端 see-through 产出 `spiritagent.2d.psd/1` 描述符（kind=psd）复用 mesh2d 行 /
 * 事件管线；本 store 只做一件事：拉 manifest 判 kind——psd 则暴露签名 PSD URL
 * 供 PuppetStage 装配，否则保持未就绪让渲染级联落到 Mesh2DCanvas（DESIGN §1.2）。
 */

import { atom } from 'nanostores'

import { $mesh2dInfo } from '@/companion/mesh2d/mesh2d-store'
import { log } from '@/shared/lib/log'

interface PuppetInfo {
  psdUrl: string | null
  contentHash: string | null
  error: string | null
}

export const $puppetInfo = atom<PuppetInfo>({ psdUrl: null, contentHash: null, error: null })

// 渲染级联的 puppet 门闩：PSD URL 已知且未出错才点亮；Stage 内装配失败会写 error 熄灭。
export const $puppetReady = atom<boolean>(false)

// 签名 URL 每 5 分钟重签；缓存键用内容寻址的 contentHash。
const KIND_CACHE = new Map<string, boolean>()

export function setPuppetError(error: string): void {
  $puppetInfo.set({ ...$puppetInfo.get(), error })
  $puppetReady.set(false)
}

export function resetPuppet(): void {
  $puppetInfo.set({ psdUrl: null, contentHash: null, error: null })
  $puppetReady.set(false)
}

/** 读取当前 2D 行的 manifest 判定 psd 链；在 hydrateMesh2D 完成后调用。 */
export async function hydratePuppet(): Promise<void> {
  const info = $mesh2dInfo.get()

  if (info.status !== 'succeeded' || !info.manifestUrl) {
    $puppetInfo.set({ psdUrl: null, contentHash: null, error: null })
    $puppetReady.set(false)

    return
  }

  const cacheKey = info.contentHash ?? info.manifestUrl
  const cached = KIND_CACHE.get(cacheKey)

  if (cached === false) {
    // 已知是 mesh2d 描述符：清空 puppet 状态即可，不重复拉 manifest。
    $puppetInfo.set({ psdUrl: null, contentHash: null, error: null })
    $puppetReady.set(false)

    return
  }

  try {
    let manifest: { schema?: string; kind?: string }

    if (typeof window !== 'undefined' && window.spiritagent?.api) {
      manifest = await window.spiritagent.api<{ schema?: string; kind?: string }>({ path: info.manifestUrl })
    } else {
      const res = await fetch(info.manifestUrl, { credentials: 'include' })

      if (!res.ok) {
        throw new Error(`manifest fetch failed: ${res.status}`)
      }

      manifest = (await res.json()) as { schema?: string; kind?: string }
    }

    const isPsd = manifest?.kind === 'psd' && (manifest.schema ?? '').startsWith('spiritagent.2d.psd')
    KIND_CACHE.set(cacheKey, isPsd)

    if (!isPsd) {
      $puppetInfo.set({ psdUrl: null, contentHash: null, error: null })
      $puppetReady.set(false)

      return
    }

    const psdUrl = info.layerUrls['psd'] ?? null

    if (!psdUrl) {
      throw new Error('psd manifest missing layer_urls.psd')
    }

    $puppetInfo.set({ psdUrl, contentHash: info.contentHash, error: null })
    $puppetReady.set(true)
    log.info('puppet-store', 'psd manifest detected')
  } catch (err) {
    // 判定失败按"非 puppet"处理：级联落到 mesh2d / 3D / 蛋，不让桌面空白。
    log.warn('puppet-store', 'hydratePuppet failed; falling back', err)
    $puppetInfo.set({ psdUrl: null, contentHash: null, error: err instanceof Error ? err.message : String(err) })
    $puppetReady.set(false)
  }
}
