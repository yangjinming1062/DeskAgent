/** 加载 manifest.json 并标记 mesh2d 已就绪。*/

import { log } from '@/shared/lib/log'

import type { Manifest } from './mesh2d-runtime'
import { $mesh2dReady } from './mesh2d-store'

const MANIFEST_CACHE = new Map<string, Manifest>()

/** 拉取并解析 manifest；失败时抛错，由调用方回退到程序化蛋。*/
export async function loadMesh2DManifest(url: string): Promise<Manifest> {
  const cached = MANIFEST_CACHE.get(url)

  if (cached) {
    if (!$mesh2dReady.get()) {
      $mesh2dReady.set(true)
    }

    return cached
  }

  const res = await fetch(url, { credentials: 'include' })

  if (!res.ok) {
    throw new Error(`manifest fetch failed: ${res.status}`)
  }

  const manifest = JSON.parse(await res.text()) as Manifest

  if (!manifest.skeleton?.bones?.length || !manifest.meshes?.length) {
    throw new Error('manifest missing skeleton.bones or meshes')
  }

  MANIFEST_CACHE.set(url, manifest)
  $mesh2dReady.set(true)
  log.info('mesh2d-loader', `loaded manifest with ${manifest.meshes.length} meshes`)

  return manifest
}
