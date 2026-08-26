import { $gateway } from '@/shared/store/gateway'
import { $runnerPhase } from '@/shared/store/runner-status'

import { $focusContext, $lastIdleSeconds, $screenLocked } from './activity'
import { $effectiveTier } from './companion-store'
import { $llmAutonomy } from './prefs'
import { $defaultScale, computePerchPlacement, setLocale, startRoam } from './spatial'

const CONSULT_MIN_INTERVAL_MS = 60_000
const MIN_ACTION_QUIET_MS = 60_000
const BACKGROUND_CONSULT_INTERVAL_MS = 30 * 60_000

interface Snapshot {
  focused_category: string
  fullscreen: boolean
  locked: boolean
}

interface ShouldActRpcResponse {
  should_act?: boolean
  action?: string
  params?: Record<string, unknown>
  reason?: string
}

let lastConsultAt = 0
let lastAutonomousActionAt = 0
let lastSnapshot: Snapshot | null = null
let backgroundTimer: ReturnType<typeof setInterval> | null = null
let unsubs: Array<() => void> = []
let active = false

function stateChanged(oldSnap: Snapshot, newSnap: Snapshot): boolean {
  return (
    oldSnap.focused_category !== newSnap.focused_category ||
    oldSnap.fullscreen !== newSnap.fullscreen ||
    oldSnap.locked !== newSnap.locked
  )
}

function executeAutonomousAction(action: string): void {
  switch (action) {
    case 'roam':
      startRoam()

      break
    case 'perch': {
      const ctx = $focusContext.get()

      if (ctx?.windowGeom && ctx.category !== 'unknown' && !ctx.fullscreen) {
        const perch = computePerchPlacement(ctx.windowGeom, $defaultScale.get())

        if (perch) {
          setLocale('perch', { position: perch.pos, scaleLimit: perch.scale })
        }
      }

      break
    }

    default:
      break
  }
}

async function consultAutonomyLLM(force = false): Promise<void> {
  // 空间智能决策只在自主档咨询——常规档不移动，静止档连主动推理都不发起
  // （DESIGN §6.2；后端 should_act 另有静止防御闸兜底非官方链路）。
  if (!$llmAutonomy.get() || $effectiveTier.get() !== 'autonomous') {
    return
  }

  const now = Date.now()

  if (now - lastConsultAt < CONSULT_MIN_INTERVAL_MS) {
    return
  }

  if (now - lastAutonomousActionAt < MIN_ACTION_QUIET_MS) {
    return
  }

  const idle = Math.max(0, $lastIdleSeconds.get())
  const focus = $focusContext.get()
  const locked = $screenLocked.get()
  const hour = new Date().getHours()

  const newSnapshot: Snapshot = {
    focused_category: focus?.category ?? 'unknown',
    fullscreen: focus?.fullscreen ?? false,
    locked
  }

  if (!force && lastSnapshot !== null && !stateChanged(lastSnapshot, newSnapshot)) {
    return
  }

  const gateway = $gateway.get()

  if (!gateway) {
    return
  }

  lastConsultAt = now
  lastSnapshot = newSnapshot

  const secondsSinceLastAction = lastAutonomousActionAt > 0 ? (now - lastAutonomousActionAt) / 1000 : 9999

  try {
    const res = await gateway.request<ShouldActRpcResponse>('companion.should_act', {
      kind: 'periodic_provision',
      idle_seconds: idle,
      local_hour: hour,
      focused_category: focus?.category ?? null,
      fullscreen: focus?.fullscreen ?? false,
      screen_locked: locked,
      seconds_since_last_action: secondsSinceLastAction
    })

    if (res?.should_act && res.action) {
      lastAutonomousActionAt = Date.now()
      executeAutonomousAction(res.action)
    }
  } catch {
    /* 静默捕获；LLM 错误时不做任何自主动作 */
  }
}

export function startAutonomyProvision(): () => void {
  if (active) {
    return stopAutonomyProvision
  }

  active = true

  const onStateOrEventChange = () => {
    if (active && $runnerPhase.get() === 'running') {
      void consultAutonomyLLM(false)
    }
  }

  unsubs.push($focusContext.subscribe(onStateOrEventChange))
  unsubs.push($screenLocked.subscribe(onStateOrEventChange))
  unsubs.push($lastIdleSeconds.subscribe(onStateOrEventChange))
  unsubs.push(
    $llmAutonomy.subscribe(enabled => {
      if (!enabled) {
        lastSnapshot = null
      } else {
        onStateOrEventChange()
      }
    })
  )

  backgroundTimer = setInterval(() => {
    if (active && $runnerPhase.get() === 'running') {
      void consultAutonomyLLM(true)
    }
  }, BACKGROUND_CONSULT_INTERVAL_MS)

  // 显式触发首次咨询，而不是依赖任一 atom 的订阅回放语义。
  if ($runnerPhase.get() === 'running') {
    void consultAutonomyLLM(true)
  }

  return stopAutonomyProvision
}

export function stopAutonomyProvision(): void {
  active = false

  if (backgroundTimer !== null) {
    clearInterval(backgroundTimer)
    backgroundTimer = null
  }

  for (const unsub of unsubs) {
    unsub()
  }

  unsubs = []
  lastSnapshot = null
}
