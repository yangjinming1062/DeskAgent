import { $screenLocked } from '@/companion/activity'
import { $chatOpen } from '@/companion/chat-store'
import { setSpriteState } from '@/companion/companion-store'
import { computePerchPosition, moveTo, reevaluateSpatialDecision } from '@/companion/spatial'
import { $staticMode } from '@/companion/static-sprite/sprite-store'
import { sleep } from '@/shared/lib/utils'

const RETRY_MS = 300
const RETRY_COUNT = 5

interface WindowGeom {
  x: number
  y: number
  w: number
  h: number
}

export type { WindowGeom }

export async function findWindowByKeyword(keyword: string): Promise<WindowGeom | null> {
  if (!window.spiritagent?.runnerInvoke) {
    return null
  }

  try {
    const result = await window.spiritagent.runnerInvoke('system.get_windows', {})
    const windows = (result as { windows?: Array<{ name: string; title: string } & WindowGeom> }).windows ?? []
    const kw = keyword.toLowerCase()

    const match = windows.find(
      w =>
        w.name.toLowerCase().includes(kw) ||
        w.title.toLowerCase().includes(kw) ||
        kw.includes(w.name.toLowerCase().split('.')[0])
    )

    return match ? { x: match.x, y: match.y, w: match.w, h: match.h } : null
  } catch {
    return null
  }
}

export async function performRitualWalk<T>(
  findTarget: () => Promise<WindowGeom | null>,
  execute: () => Promise<T>
): Promise<T> {
  if ($chatOpen.get() || $screenLocked.get() || $staticMode.get()) {
    return execute()
  }

  let geom = await findTarget()

  for (let attempt = 0; !geom && attempt < RETRY_COUNT; attempt++) {
    await sleep(RETRY_MS)
    geom = await findTarget()
  }

  if (!geom) {
    return execute()
  }

  const perch = computePerchPosition(geom)

  if (!perch) {
    return execute()
  }

  await new Promise<void>(resolve => moveTo(perch, 'fly', resolve))

  setSpriteState('interacting', { durationMs: 1500 })

  const targetCenterX = Math.round(geom.x + geom.w / 2)
  const targetCenterY = Math.round(geom.y + geom.h / 2)

  if (window.spiritagent?.runnerInvoke) {
    window.spiritagent.runnerInvoke('system.click_at', { x: targetCenterX, y: targetCenterY }).catch(() => {})
  }

  await sleep(400)

  const result = await execute()

  await sleep(800)
  reevaluateSpatialDecision()

  return result
}
