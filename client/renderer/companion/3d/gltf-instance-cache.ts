import * as THREE from 'three'

import { log } from '@/shared/lib/log'

/** Detached GLB instance cache, modelled on airi's `vrm-instance-cache.ts`. Keeps parsed scene + animations in a module-level Map keyed by `contentHash`; clones the template on cache hit. Cloned meshes share materials/textures with the template (read-only — PBR hot-swap goes through `loadPbrChannel`). The companion never has more than one active model per template. */

interface CachedTemplate {
  scene: THREE.Group
  animations: THREE.AnimationClip[]
  bytes: number
  hits: number
}

const _cache = new Map<string, CachedTemplate>()

/** Stash a freshly parsed GLB scene as the template for `key`. Subsequent `takeGltfClone(key)` returns a deep clone. */
export function stashGltf(key: string, gltf: THREE.Group, animations: THREE.AnimationClip[], bytes = 0): void {
  if (!key) {
    return
  }

  const prev = _cache.get(key)

  if (prev) {
    disposeTemplate(prev)
  }

  _cache.set(key, { scene: gltf, animations, bytes, hits: 0 })
}

/** Pull a deep clone of the cached template for `key`. Returns null on miss. Multiple clones of the same template co-exist safely (shared materials, read-only). */
export function takeGltfClone(key: string): { scene: THREE.Group; animations: THREE.AnimationClip[] } | null {
  if (!key) {
    return null
  }

  const cached = _cache.get(key)

  if (!cached) {
    return null
  }

  cached.hits++

  return {
    scene: cached.scene.clone(true),
    animations: cached.animations
  }
}

export function hasGltf(key: string): boolean {
  return _cache.has(key)
}

/** Drop a single template from the cache, freeing its GPU resources. Safe to call on a non-existent key. */
export function clearGltf(key: string): void {
  const cached = _cache.get(key)

  if (cached) {
    disposeTemplate(cached)
    _cache.delete(key)
  }
}

/** Drop every cached template. Useful when the user signs out or the renderer tears down. */
export function clearAllGltf(): void {
  for (const cached of _cache.values()) {
    disposeTemplate(cached)
  }

  _cache.clear()
}

/** Diagnostic snapshot. The dev overlay can read this to show cache pressure. */
export function gltfCacheStats(): { keys: string[]; totalBytes: number; totalHits: number } {
  let totalBytes = 0
  let totalHits = 0

  for (const cached of _cache.values()) {
    totalBytes += cached.bytes
    totalHits += cached.hits
  }

  return { keys: [..._cache.keys()], totalBytes, totalHits }
}

function disposeTemplate(cached: CachedTemplate): void {
  try {
    cached.scene.traverse(child => {
      if (child instanceof THREE.Mesh) {
        child.geometry?.dispose()
      }
    })
  } catch (err) {
    log.warn('gltf-instance-cache', 'template dispose failed:', err)
  }
}
