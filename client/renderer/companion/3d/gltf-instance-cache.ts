import * as THREE from 'three'
import { clone as cloneSkeleton } from 'three/addons/utils/SkeletonUtils.js'

import { log } from '@/shared/lib/log'

/**
 * 客户端 GLB 模板缓存与资源所有权管理。
 * - 模板 Cache 拥有原始 GLB 的 geometry、material、texture 等 GPU 资源；
 * - 活跃实例通过 `takeGltfClone(key)` 取出深克隆对象（网格、骨骼与层级独立重建），并增加模板引用计数；
 * - 活跃实例卸载时调用 `releaseGltf(key)` 递减引用计数，不得释放模板持有的共享资源；
 * - 仅当模板引用计数降为 0 时，Cache 清理（`clearGltf` / `clearAllGltf` / LRU prune）才会真正执行 GPU 资源释放。
 */

export const DEFAULT_MAX_TEMPLATES = 3
export const DEFAULT_MAX_CACHE_BYTES = 128 * 1024 * 1024 // 128 MB

const COMMON_TEXTURE_KEYS = [
  'map',
  'alphaMap',
  'aoMap',
  'bumpMap',
  'displacementMap',
  'emissiveMap',
  'envMap',
  'lightMap',
  'metalnessMap',
  'normalMap',
  'roughnessMap',
  'specularMap',
  'clearcoatMap',
  'clearcoatRoughnessMap',
  'clearcoatNormalMap',
  'sheenColorMap',
  'sheenRoughnessMap',
  'transmissionMap',
  'thicknessMap',
  'iridescenceMap',
  'iridescenceThicknessMap',
  'anisotropyMap'
] as const

/**
 * 递归释放 Object3D 层级树下的几何体、材质以及常见 PBR 贴图。
 * 使用 Set 记录已访问的 GPU 资源指针，防止共享材质或贴图被重复调用 dispose()。
 */
export function disposeThreeResources(root: THREE.Object3D): void {
  const disposedGeometries = new Set<THREE.BufferGeometry>()
  const disposedMaterials = new Set<THREE.Material>()
  const disposedTextures = new Set<THREE.Texture>()

  const disposeTexture = (texture: unknown): void => {
    if (texture && texture instanceof THREE.Texture && !disposedTextures.has(texture)) {
      disposedTextures.add(texture)

      try {
        texture.dispose()
      } catch (err) {
        log.warn('gltf-instance-cache', 'texture dispose failed:', err)
      }
    }
  }

  const disposeMaterial = (material: THREE.Material): void => {
    if (!material || disposedMaterials.has(material)) {
      return
    }

    disposedMaterials.add(material)

    const matRecord = material as unknown as Record<string, unknown>

    for (const key of COMMON_TEXTURE_KEYS) {
      disposeTexture(matRecord[key])
    }

    if ('uniforms' in material && typeof matRecord.uniforms === 'object' && matRecord.uniforms !== null) {
      const uniforms = matRecord.uniforms as Record<string, { value?: unknown }>

      for (const uniform of Object.values(uniforms)) {
        if (uniform && typeof uniform === 'object' && 'value' in uniform) {
          disposeTexture(uniform.value)
        }
      }
    }

    try {
      material.dispose()
    } catch (err) {
      log.warn('gltf-instance-cache', 'material dispose failed:', err)
    }
  }

  try {
    root.traverse(child => {
      if ('geometry' in child && child.geometry instanceof THREE.BufferGeometry) {
        if (!disposedGeometries.has(child.geometry)) {
          disposedGeometries.add(child.geometry)

          try {
            child.geometry.dispose()
          } catch (err) {
            log.warn('gltf-instance-cache', 'geometry dispose failed:', err)
          }
        }
      }

      if ('material' in child && child.material) {
        if (Array.isArray(child.material)) {
          for (const mat of child.material) {
            if (mat instanceof THREE.Material) {
              disposeMaterial(mat)
            }
          }
        } else if (child.material instanceof THREE.Material) {
          disposeMaterial(child.material)
        }
      }

      if (child instanceof THREE.SkinnedMesh && child.skeleton) {
        try {
          child.skeleton.dispose()
        } catch {}
      }
    })
  } catch (err) {
    log.warn('gltf-instance-cache', 'disposeThreeResources traversal failed:', err)
  }
}

export interface CachedTemplate {
  animations: THREE.AnimationClip[]
  bytes: number
  hits: number
  lastUsed: number
  pendingDispose?: boolean
  refCount: number
  scene: THREE.Group
}

const _cache = new Map<string, CachedTemplate>()
const _pendingTemplates = new Map<string, CachedTemplate[]>()

function addPendingTemplate(key: string, template: CachedTemplate): void {
  template.pendingDispose = true
  const list = _pendingTemplates.get(key) ?? []
  list.push(template)
  _pendingTemplates.set(key, list)
}

/** 存入已解析的 GLB 场景作为模板。后续 `takeGltfClone(key)` 返回深克隆。 */
export function stashGltf(
  key: string,
  gltf: THREE.Group,
  animations: THREE.AnimationClip[],
  bytes = 0,
  maxTemplates = DEFAULT_MAX_TEMPLATES,
  maxBytes = DEFAULT_MAX_CACHE_BYTES
): void {
  if (!key) {
    return
  }

  const prev = _cache.get(key)

  if (prev) {
    if (prev.scene === gltf) {
      prev.bytes = bytes || prev.bytes
      prev.lastUsed = Date.now()

      return
    }

    if (prev.refCount === 0) {
      disposeTemplate(prev)
    } else {
      addPendingTemplate(key, prev)
    }

    _cache.delete(key)
  }

  pruneTemplates(maxTemplates - 1, maxBytes - bytes)

  _cache.set(key, {
    animations,
    bytes,
    hits: 0,
    lastUsed: Date.now(),
    refCount: 0,
    scene: gltf
  })
}

/**
 * 淘汰超出容量限制的未引用模板（`refCount === 0` 且最近最少使用优先）。
 */
export function pruneTemplates(maxTemplates = DEFAULT_MAX_TEMPLATES, maxBytes = DEFAULT_MAX_CACHE_BYTES): void {
  let totalBytes = 0

  for (const item of _cache.values()) {
    totalBytes += item.bytes
  }

  if (_cache.size <= maxTemplates && totalBytes <= maxBytes) {
    return
  }

  const candidates: { key: string; template: CachedTemplate }[] = []

  for (const [key, item] of _cache.entries()) {
    if (item.refCount === 0) {
      candidates.push({ key, template: item })
    }
  }

  candidates.sort((a, b) => a.template.lastUsed - b.template.lastUsed)

  for (const { key, template } of candidates) {
    if (_cache.size <= maxTemplates && totalBytes <= maxBytes) {
      break
    }

    disposeTemplate(template)
    _cache.delete(key)
    totalBytes -= template.bytes
  }
}

/**
 * 取出缓存模板的深克隆对象。
 * - 使用 `SkeletonUtils.clone` 确保 SkinnedMesh 与 Bone 层级结构重新映射；
 * - 独立克隆 AnimationClip 状态；
 * - 增加模板引用计数 `refCount`。
 */
export function takeGltfClone(key: string): { animations: THREE.AnimationClip[]; scene: THREE.Group } | null {
  if (!key) {
    return null
  }

  const cached = _cache.get(key)

  if (!cached || cached.pendingDispose) {
    return null
  }

  cached.hits++
  cached.lastUsed = Date.now()
  cached.refCount++

  const clonedScene = cloneSkeleton(cached.scene) as THREE.Group
  const clonedAnimations = cached.animations.map(clip => clip.clone())

  return {
    animations: clonedAnimations,
    scene: clonedScene
  }
}

/** 递减模板的活跃引用计数。当计数归零且被标记待释放时触发实际 GPU 销毁。 */
export function releaseGltf(key: string): void {
  if (!key) {
    return
  }

  const cached = _cache.get(key)

  if (cached) {
    cached.refCount = Math.max(0, cached.refCount - 1)

    if (cached.refCount === 0 && cached.pendingDispose) {
      disposeTemplate(cached)
      _cache.delete(key)
    }
  }

  const pendingList = _pendingTemplates.get(key)

  if (pendingList) {
    const remaining: CachedTemplate[] = []

    for (const pending of pendingList) {
      pending.refCount = Math.max(0, pending.refCount - 1)

      if (pending.refCount === 0) {
        disposeTemplate(pending)
      } else {
        remaining.push(pending)
      }
    }

    if (remaining.length > 0) {
      _pendingTemplates.set(key, remaining)
    } else {
      _pendingTemplates.delete(key)
    }
  }
}

export function hasGltf(key: string): boolean {
  const cached = _cache.get(key)

  return Boolean(cached && !cached.pendingDispose)
}

/** 从缓存中移除单个模板。若仍有活跃引用则延迟释放。 */
export function clearGltf(key: string, force = false): void {
  const cached = _cache.get(key)

  if (!cached) {
    return
  }

  if (cached.refCount === 0 || force) {
    disposeTemplate(cached)
    _cache.delete(key)
  } else {
    addPendingTemplate(key, cached)
    _cache.delete(key)
  }
}

/** 清空所有缓存模板。用于用户登出或渲染器完全销毁。 */
export function clearAllGltf(force = false): void {
  for (const [key, cached] of _cache.entries()) {
    if (cached.refCount === 0 || force) {
      disposeTemplate(cached)
    } else {
      addPendingTemplate(key, cached)
    }
  }

  _cache.clear()

  if (force) {
    for (const list of _pendingTemplates.values()) {
      for (const pending of list) {
        disposeTemplate(pending)
      }
    }

    _pendingTemplates.clear()
  }
}

/** 获取模板缓存状态快照。 */
export function gltfCacheStats(): { activeRefs: number; keys: string[]; totalBytes: number; totalHits: number } {
  let totalBytes = 0
  let totalHits = 0
  let activeRefs = 0

  for (const cached of _cache.values()) {
    totalBytes += cached.bytes
    totalHits += cached.hits
    activeRefs += cached.refCount
  }

  for (const list of _pendingTemplates.values()) {
    for (const pending of list) {
      activeRefs += pending.refCount
    }
  }

  return { activeRefs, keys: [..._cache.keys()], totalBytes, totalHits }
}

function disposeTemplate(cached: CachedTemplate): void {
  try {
    disposeThreeResources(cached.scene)
  } catch (err) {
    log.warn('gltf-instance-cache', 'template dispose failed:', err)
  }
}
