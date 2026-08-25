import { atom } from 'nanostores'

import { resolvePortraitUrl } from '@/companion/avatar-image'
import { hydrateMesh2D } from '@/companion/mesh2d/mesh2d-store'
import { isClientErrorIpc } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'

export type OutfitStatus = 'draft' | 'splitting' | 'ready' | 'failed' | 'expired'

export interface WardrobeOutfit {
  id: number
  name: string
  description: string | null
  fullbodyUrl: string | null
  style: string
  status: OutfitStatus
  active: boolean
  pendingWear: boolean
}

interface OutfitResponse {
  id: number
  name: string
  description: string | null
  fullbody_url: string
  style: string
  status: string
  active: boolean
  pending_wear: boolean
}

export const $outfits = atom<WardrobeOutfit[]>([])

async function fetchOutfits(): Promise<WardrobeOutfit[]> {
  const res = await window.spiritagent.api<{ outfits: OutfitResponse[] }>({ path: '/api/companion/outfits' })

  return Promise.all(
    (res?.outfits ?? []).map(
      async (o): Promise<WardrobeOutfit> => ({
        id: o.id,
        name: o.name,
        description: o.description ?? null,
        fullbodyUrl: await resolvePortraitUrl(o.fullbody_url),
        style: o.style || 'cel_shading',
        status: (o.status || 'draft') as OutfitStatus,
        active: o.active === true,
        pendingWear: o.pending_wear === true
      })
    )
  )
}

export async function hydrateWardrobe(): Promise<void> {
  try {
    $outfits.set(await fetchOutfits())
  } catch (err) {
    if (!isClientErrorIpc(err)) {
      log.warn('wardrobe', 'hydrateWardrobe failed', err)
    }
  }
}

/** 穿着就绪外观；成功后整包替换 2D 资产（Mesh2DCanvas 按 manifestUrl 重建，期间旧装不断档）。 */
export async function activateOutfit(outfitId: number): Promise<boolean> {
  try {
    await window.spiritagent.api({ path: `/api/companion/outfits/${outfitId}/activate`, method: 'PUT' })
    await hydrateWardrobe()
    await hydrateMesh2D()

    return true
  } catch (err) {
    log.warn('wardrobe', 'activateOutfit failed', err)

    return false
  }
}

export async function deleteOutfit(outfitId: number): Promise<boolean> {
  try {
    await window.spiritagent.api({ path: `/api/companion/outfits/${outfitId}`, method: 'DELETE' })
    await hydrateWardrobe()

    return true
  } catch (err) {
    log.warn('wardrobe', 'deleteOutfit failed', err)

    return false
  }
}
