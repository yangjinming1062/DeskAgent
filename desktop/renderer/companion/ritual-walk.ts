import { $screenLocked } from '@/companion/activity'
import { $chatOpen } from '@/companion/chat-store'
import { setSpriteState } from '@/companion/companion-store'
import { computePerchPosition, moveTo, reevaluateSpatialDecision } from '@/companion/spatial'

const RETRY_MS = 300
const RETRY_COUNT = 5

interface WindowGeom {
  x: number
  y: number
  w: number
  h: number
}

export async function findWindowByKeyword(keyword: string): Promise<WindowGeom | null> {
  if (!window.deskagent?.runnerInvoke) {
    return null
  }

  try {
    const result = await window.deskagent.runnerInvoke('system.get_windows', {})
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
  if ($chatOpen.get() || $screenLocked.get()) {
    return execute()
  }

  const result = await execute()

  let geom = await findTarget()

  for (let attempt = 0; !geom && attempt < RETRY_COUNT; attempt++) {
    await new Promise(resolve => setTimeout(resolve, RETRY_MS))
    geom = await findTarget()
  }

  if (!geom) {
    return result
  }

  const perch = computePerchPosition(geom)

  if (!perch) {
    return result
  }

  await new Promise<void>(resolve => moveTo(perch, 'fly', resolve))

  setSpriteState('interacting', { durationMs: 1500 })
  await new Promise(resolve => setTimeout(resolve, 1200))

  reevaluateSpatialDecision()

  return result
}
