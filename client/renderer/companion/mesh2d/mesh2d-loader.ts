/** 加载 manifest.json 并标记 mesh2d 已就绪。*/

import { log } from '@/shared/lib/log'

import type { ActionDef, ActionTrack } from './mesh2d-drivers'
import type { Manifest } from './mesh2d-runtime'
import { $mesh2dReady } from './mesh2d-store'

// 签名 URL 每 5 分钟重签会变；缓存键优先用内容寻址的 contentHash。
const MANIFEST_CACHE = new Map<string, Manifest>()

interface V2BoneTransform {
  rotation_rad?: { x?: number; y?: number; z?: number }
  scale?: { x?: number; y?: number; z?: number }
  position_offset?: { x?: number; y?: number }
}

type RawAction = ActionDef & { bones?: Record<string, V2BoneTransform> }

/** v2 静态 pose 表 → v3 单关键帧 tracks（每 channel/axis 一轨、t=0 保持）；v3 原样通过。 */
function normalizeActions(raw: Record<string, unknown> | undefined): Record<string, unknown> {
  const out: Record<string, unknown> = {}

  for (const [name, value] of Object.entries(raw ?? {})) {
    const action = value as RawAction

    if (Array.isArray(action.tracks)) {
      out[name] = action

      continue
    }

    const tracks: ActionTrack[] = []

    for (const [bone, tx] of Object.entries(action.bones ?? {})) {
      for (const axis of ['x', 'y', 'z'] as const) {
        const rot = tx.rotation_rad?.[axis]

        if (rot !== undefined) {
          tracks.push({ bone, channel: 'rotation', axis, keys: [{ t_ms: 0, v: rot }] })
        }

        const scale = tx.scale?.[axis]

        if (scale !== undefined) {
          tracks.push({ bone, channel: 'scale', axis, keys: [{ t_ms: 0, v: scale }] })
        }

        if (axis !== 'z') {
          const pos = tx.position_offset?.[axis]

          if (pos !== undefined) {
            tracks.push({ bone, channel: 'position', axis, keys: [{ t_ms: 0, v: pos }] })
          }
        }
      }
    }

    out[name] = {
      duration_ms: action.duration_ms,
      blend_in_ms: action.blend_in_ms,
      blend_out_ms: action.blend_out_ms,
      loop: action.loop,
      tracks
    }
  }

  return out
}

/** 拉取并解析 manifest；失败时抛错，由调用方回退到程序化蛋。*/
export async function loadMesh2DManifest(url: string, contentHash?: string): Promise<Manifest> {
  const cacheKey = contentHash ?? url
  const cached = MANIFEST_CACHE.get(cacheKey)

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

  if ((manifest.version ?? 2) > 3) {
    log.warn('mesh2d-loader', `manifest version ${manifest.version} newer than supported; best-effort`)
  }

  manifest.animations = { ...manifest.animations, actions: normalizeActions(manifest.animations.actions) }

  MANIFEST_CACHE.set(cacheKey, manifest)
  $mesh2dReady.set(true)
  log.info('mesh2d-loader', `loaded manifest with ${manifest.meshes.length} meshes`)

  return manifest
}
