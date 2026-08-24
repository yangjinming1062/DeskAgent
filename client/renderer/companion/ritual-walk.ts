import { $screenLocked } from '@/companion/activity'
import { $chatOpen } from '@/companion/chat-store'
import { $spriteAction, clearGazeTarget, setGazeTarget, setSpriteState } from '@/companion/companion-store'
import {
  $spatialPos,
  $spatialScale,
  computePerchPosition,
  getBaseSpriteHeight,
  getBaseSpriteWidth,
  moveTo,
  reevaluateSpatialDecision
} from '@/companion/spatial'
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

/** 屏幕坐标 → 精灵窗口归一 [-1,1] 的视线目标。粗粒度方向感即可，clamp 到边界防越轴。 */
function gazeTowardsPoint(point: { x: number; y: number }): { nx: number; ny: number } {
  const pos = $spatialPos.get()
  const halfW = (getBaseSpriteWidth() * $spatialScale.get()) / 2
  const halfH = (getBaseSpriteHeight() * $spatialScale.get()) / 2

  const clamp = (v: number): number => Math.max(-1, Math.min(1, v))

  return {
    nx: clamp((point.x - (pos.x + halfW)) / Math.max(halfW, 1)),
    ny: clamp((point.y - (pos.y + halfH)) / Math.max(halfH, 1))
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

  // 飞行途中视线锁定目标窗口中心；抵达后按方位抬手指向，再接 click 触碰
  const targetCenter = { x: geom.x + geom.w / 2, y: geom.y + geom.h / 2 }
  setGazeTarget(gazeTowardsPoint(targetCenter))

  try {
    await new Promise<void>(resolve => moveTo(perch, 'fly', resolve))

    const dx = targetCenter.x - ($spatialPos.get().x + getBaseSpriteWidth() / 2)
    $spriteAction.set(dx >= 0 ? 'point_right' : 'point_left')
    await sleep(800)

    // DESIGN §3.6：抵达后播放专属「点击/触碰」肢体动作，
    // 与通用 interacting 状态区别开来——动作优先于状态动画。
    $spriteAction.set('click')
    setSpriteState('interacting', { durationMs: 1500 })

    if (window.spiritagent?.runnerInvoke) {
      window.spiritagent
        .runnerInvoke('system.click_at', { x: Math.round(targetCenter.x), y: Math.round(targetCenter.y) })
        .catch(() => {})
    }

    await sleep(400)

    const result = await execute()

    // 执行结束后清除 click action，让后续状态机正常推进
    if ($spriteAction.get() === 'click') {
      $spriteAction.set(null)
    }

    return result
  } finally {
    // gaze 泄漏会让精灵永远盯着最后的目标；异常路径同样要解锁
    clearGazeTarget()
    await sleep(800)
    reevaluateSpatialDecision()
  }
}
