import { $screenLocked } from '@/companion/activity'
import { $chatOpen } from '@/companion/chat-store'
import { $spriteAction, setSpriteState } from '@/companion/companion-store'
import { computePerchPosition, moveTo, reevaluateSpatialDecision } from '@/companion/spatial'
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
  if ($chatOpen.get() || $screenLocked.get()) {
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

  // DESIGN §3.6：抵达后播放专属「点击/触碰」肢体动作，
  // 与通用 interacting 状态区别开来——动作优先于状态动画。
  $spriteAction.set('click')
  setSpriteState('interacting', { durationMs: 1500 })

  const targetCenterX = Math.round(geom.x + geom.w / 2)
  const targetCenterY = Math.round(geom.y + geom.h / 2)

  if (window.spiritagent?.runnerInvoke) {
    window.spiritagent.runnerInvoke('system.click_at', { x: targetCenterX, y: targetCenterY }).catch(() => {})
  }

  await sleep(400)

  const result = await execute()

  // 执行结束后清除 click action，让后续状态机正常推进
  if ($spriteAction.get() === 'click') {
    $spriteAction.set(null)
  }

  await sleep(800)
  reevaluateSpatialDecision()

  return result
}
