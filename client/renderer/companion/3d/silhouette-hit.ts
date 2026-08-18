import { atom } from 'nanostores'
import { MathUtils } from 'three'

import { probeInteractiveRegions } from '@/companion/interactive-regions'

import type { Engine, SilhouetteHitmap } from './Engine'

// Sync hit predicate SpriteStage refines its region with in 3D mode. Null during boot/load gap -> rect fallback.
export const $sprite3DHitTest = atom<((x: number, y: number) => boolean | null) | null>(null)

const HIT_ALPHA_MIN = 16

export function attachSilhouetteHitProbe(
  engine: Pick<Engine, 'canvas' | 'silhouetteHitmap'> & { getSilhouetteHitmap?: () => SilhouetteHitmap | null }
): () => void {
  let latestMap: SilhouetteHitmap | null = null
  let refreshing = false
  let disposed = false
  let rafId: number | null = null

  const test = (x: number, y: number): boolean | null => {
    const map = engine.getSilhouetteHitmap?.() ?? latestMap

    if (!map) {
      return null
    }

    const canvas = engine.canvas
    const rect = canvas.getBoundingClientRect()

    if (rect.width <= 0 || rect.height <= 0) {
      return false
    }

    if (x < rect.left || x > rect.right || y < rect.top || y > rect.bottom) {
      return false
    }

    const relX = (x - rect.left) / rect.width
    const relY = (y - rect.top) / rect.height
    // Engine normalizes hitmap alpha to top-down row order matching DOM client space.
    const px = MathUtils.clamp(Math.floor(relX * map.width), 0, map.width - 1)
    const py = MathUtils.clamp(Math.floor(relY * map.height), 0, map.height - 1)

    return map.alpha[py * map.width + px] >= HIT_ALPHA_MIN
  }

  const refresh = (): void => {
    if (refreshing || disposed) {
      return
    }

    refreshing = true
    void engine
      .silhouetteHitmap()
      .then(map => {
        if (disposed) {
          return
        }

        if (map) {
          latestMap = map
          probeInteractiveRegions()
        }
      })
      .finally(() => {
        refreshing = false
      })
  }

  const onMove = (e: MouseEvent): void => {
    const rect = engine.canvas.getBoundingClientRect()

    // When the cursor is within or approaching canvas bounds, refresh hitmap if stale
    if (
      e.clientX >= rect.left - 50 &&
      e.clientX <= rect.right + 50 &&
      e.clientY >= rect.top - 50 &&
      e.clientY <= rect.bottom + 50
    ) {
      if (rafId === null) {
        rafId = requestAnimationFrame(() => {
          rafId = null
          refresh()
        })
      }
    }
  }

  // Request initial hitmap right away so it is warm as soon as the engine renders
  refresh()

  window.addEventListener('mousemove', onMove)
  $sprite3DHitTest.set(test)

  return () => {
    disposed = true
    window.removeEventListener('mousemove', onMove)

    if (rafId !== null) {
      cancelAnimationFrame(rafId)
      rafId = null
    }

    $sprite3DHitTest.set(null)
    latestMap = null
  }
}
