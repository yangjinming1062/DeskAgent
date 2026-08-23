import { atom } from 'nanostores'

import { $focusContext } from '@/companion/activity'
import { $chatOpen } from '@/companion/chat-store'
import {
  $clipOverride,
  $effectiveTier,
  $spriteEmotion,
  $spriteState,
  setSpriteState
} from '@/companion/companion-store'
import { $llmAutonomy } from '@/companion/prefs'
import { persistString, storedString } from '@/shared/lib/storage'

export function getBaseSpriteHeight(): number {
  if (typeof window === 'undefined') {
    return 360
  }

  // 默认高度为显示器高度的 1/3，限制在 [260, 960] 区间内
  return Math.round(Math.max(260, Math.min(window.innerHeight / 3, 960)))
}

export function getBaseSpriteWidth(): number {
  return Math.round(getBaseSpriteHeight() * 0.85)
}

export const SPRITE_W = typeof window !== 'undefined' ? getBaseSpriteWidth() : 306
export const SPRITE_H = typeof window !== 'undefined' ? getBaseSpriteHeight() : 360
const REST_MARGIN = 24

const WALK_SPEED = 80
const FLY_SPEED = 400
const SCALE_TRANSITION_MS = 300
const SCALE_KEY = 'da.companion.defaultScale'

// 高唤醒度内置情绪的瞬时缩放因子。
const EMOTION_SCALE_BOOST: Record<string, number> = {
  excited: 1.5,
  playful: 1.3,
  surprised: 1.6
}

export const MIN_SCALE = 0.5
export const MAX_SCALE = 2

export type SpatialLocale = 'home' | 'perch' | 'target' | 'roam'
export type Locomotion = 'still' | 'walk' | 'fly' | 'drag'

export const $spatialLocale = atom<SpatialLocale>('home')
export const $spatialPos = atom<{ x: number; y: number }>(getHomePosition())
export const $homePosition = atom<{ x: number; y: number }>(getHomePosition())
export const $defaultScale = atom<number>(readDefaultScale())
export const $spatialScale = atom<number>($defaultScale.get())
export const $spatialLocomotion = atom<Locomotion>('still')
export const $dragVelocity = atom<{ vx: number; vy: number }>({ vx: 0, vy: 0 })

// 窗口视口尺寸——单一真实源，由 initSpatial 已有的 resize 监听器更新。
// 弹层（chat-dock、proactive 气泡）订阅这里而不是各自挂监听器。
export interface ViewportSize {
  width: number
  height: number
}

export const $viewport = atom<ViewportSize>(
  typeof window === 'undefined' ? { width: 0, height: 0 } : { width: window.innerWidth, height: window.innerHeight }
)

export function getHomePosition(): { x: number; y: number } {
  if (typeof window === 'undefined') {
    return { x: 0, y: 0 }
  }

  const w = getBaseSpriteWidth()
  const h = getBaseSpriteHeight()

  return {
    x: Math.max(REST_MARGIN, window.innerWidth - w - REST_MARGIN),
    y: Math.max(REST_MARGIN, window.innerHeight - h - REST_MARGIN)
  }
}

export function computePerchPosition(geom: {
  x: number
  y: number
  w: number
  h: number
}): { x: number; y: number } | null {
  if (typeof window === 'undefined') {
    return null
  }

  const margin = 8
  let x = geom.x + geom.w + margin

  const y = Math.max(
    REST_MARGIN,
    Math.min(geom.y + geom.h - SPRITE_H - margin, window.innerHeight - SPRITE_H - REST_MARGIN)
  )

  if (x + SPRITE_W > window.innerWidth - REST_MARGIN) {
    x = geom.x - SPRITE_W - margin
  }

  if (x < REST_MARGIN) {
    return null
  }

  return { x, y }
}

// 精灵旁边浮动的瞬时弹层（聊天面板、proactive 气泡）的锚点定位：默认放在精灵右侧，
// 放不下则翻转到左侧。`gap` 是精灵与弹层之间的间距；`overlayMaxW` 是弹层最大可能宽度
// （仅用于翻转判定）。`top` 锚定到精灵头部区域（top + verticalRatio * 缩放后高度），
// 并限制在视口范围内。
export function computeOverlayAnchorBesideSprite(opts: {
  pos: { x: number; y: number }
  scale: number
  gap: number
  overlayMaxW: number
  overlayH?: number
  vw: number
  vh: number
  verticalRatio?: number
}): { left: number; top: number } {
  const { pos, scale, gap, overlayMaxW, overlayH = 0, vw, vh, verticalRatio = 0 } = opts
  const spriteW = SPRITE_W * scale
  const spriteH = SPRITE_H * scale
  const spriteRight = pos.x + spriteW
  const fitsRight = spriteRight + gap + overlayMaxW <= vw

  const left = fitsRight ? spriteRight + gap : Math.max(0, pos.x - gap - overlayMaxW)

  const top = Math.max(
    0,
    overlayH > 0 ? Math.min(vh - overlayH, pos.y + spriteH * verticalRatio) : pos.y + spriteH * verticalRatio
  )

  return { left, top }
}

function easeInOut(t: number): number {
  return t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2
}

let rafId: number | null = null
let moveStart: { x: number; y: number } | null = null
let moveTarget: { x: number; y: number } | null = null
let moveStartTime = 0
let moveDuration = 0
let moveOnArrive: (() => void) | null = null

function tick(now: number): void {
  if (!moveStart || !moveTarget) {
    rafId = null

    return
  }

  const t = Math.min(1, (now - moveStartTime) / moveDuration)
  const eased = easeInOut(t)

  $spatialPos.set({
    x: moveStart.x + (moveTarget.x - moveStart.x) * eased,
    y: moveStart.y + (moveTarget.y - moveStart.y) * eased
  })

  if (t < 1) {
    rafId = requestAnimationFrame(tick)
  } else {
    const cb = moveOnArrive
    moveStart = null
    moveTarget = null
    moveOnArrive = null
    rafId = null
    $spatialLocomotion.set('still')
    cb?.()
  }
}

export function moveTo(target: { x: number; y: number }, locomotion: 'walk' | 'fly', onArrive?: () => void): void {
  cancelMovement()

  const current = $spatialPos.get()
  const dist = Math.hypot(target.x - current.x, target.y - current.y)

  if (dist < 2) {
    onArrive?.()

    return
  }

  const speed = locomotion === 'walk' ? WALK_SPEED : FLY_SPEED

  moveStart = { ...current }
  moveTarget = target
  moveStartTime = performance.now()
  moveDuration = Math.max((dist / speed) * 1000, 200)
  moveOnArrive = onArrive ?? null
  $spatialLocomotion.set(locomotion)
  rafId = requestAnimationFrame(tick)
}

export function cancelMovement(): void {
  if (rafId !== null) {
    cancelAnimationFrame(rafId)
    rafId = null
  }

  const cb = moveOnArrive
  moveStart = null
  moveTarget = null
  moveOnArrive = null

  if ($spatialLocomotion.get() !== 'drag') {
    $spatialLocomotion.set('still')
  }

  cb?.()
}

let scaleRafId: number | null = null
let scaleStartVal = 1
let scaleTargetVal = 1
let scaleStartTime = 0

function tickScale(now: number): void {
  const t = Math.min(1, (now - scaleStartTime) / SCALE_TRANSITION_MS)
  const eased = easeInOut(t)

  $spatialScale.set(scaleStartVal + (scaleTargetVal - scaleStartVal) * eased)

  if (t < 1) {
    scaleRafId = requestAnimationFrame(tickScale)
  } else {
    scaleRafId = null
  }
}

export function setScaleTarget(scale: number, instant = false): void {
  const clamped = Math.max(MIN_SCALE, Math.min(MAX_SCALE, scale))

  if (instant || Math.abs(clamped - $spatialScale.get()) < 0.01) {
    if (scaleRafId !== null) {
      cancelAnimationFrame(scaleRafId)
      scaleRafId = null
    }

    $spatialScale.set(clamped)

    return
  }

  scaleStartVal = $spatialScale.get()
  scaleTargetVal = clamped
  scaleStartTime = performance.now()

  if (scaleRafId === null) {
    scaleRafId = requestAnimationFrame(tickScale)
  }
}

function readDefaultScale(): number {
  const stored = storedString(SCALE_KEY)

  if (stored) {
    const n = Number(stored)

    if (!Number.isNaN(n) && n >= MIN_SCALE && n <= MAX_SCALE) {
      return n
    }
  }

  return 1
}

function computeTargetScale(): number {
  const base = $defaultScale.get()

  if ($effectiveTier.get() === 'quiet') {
    return base
  }

  const emotion = $spriteEmotion.get()

  if ($spriteState.get() === 'emotional' && emotion) {
    const factor = EMOTION_SCALE_BOOST[emotion]

    return factor ? Math.min(base * factor, MAX_SCALE) : base
  }

  return base
}

function updateAdaptiveScale(): void {
  setScaleTarget(computeTargetScale())
}

export function setDefaultScale(scale: number): void {
  const clamped = Math.max(MIN_SCALE, Math.min(MAX_SCALE, scale))
  $defaultScale.set(clamped)
  persistString(SCALE_KEY, String(clamped))
  updateAdaptiveScale()
}

function localePosition(locale: SpatialLocale): { x: number; y: number } {
  switch (locale) {
    case 'home':
      return $homePosition.get()

    default:
      return $homePosition.get()
  }
}

function defaultLocomotion(locale: SpatialLocale): 'walk' | 'fly' | 'still' {
  switch (locale) {
    case 'target':
      return 'fly'

    default:
      return 'walk'
  }
}

export function setLocale(
  locale: SpatialLocale,
  opts?: {
    position?: { x: number; y: number }
    locomotion?: 'walk' | 'fly'
    instant?: boolean
    onArrive?: () => void
  }
): void {
  $spatialLocale.set(locale)

  const target = opts?.position ?? localePosition(locale)
  const locomotion = opts?.locomotion ?? defaultLocomotion(locale)

  if (opts?.instant || locomotion === 'still') {
    cancelMovement()
    $spatialPos.set(target)
    $spatialLocomotion.set('still')
    opts?.onArrive?.()
  } else {
    moveTo(target, locomotion, opts?.onArrive)
  }
}

let userInteracted = false

function updateSpatialDecision(): void {
  if ($spatialLocomotion.get() === 'drag' || $chatOpen.get()) {
    return
  }

  const state = $spriteState.get()

  // LLM 自主模式负责 perch/roam/home 的切换；这里只保留「安静」档位的硬约束
  // （这是用户偏好，LLM 没有上下文可以参考）。
  if ($llmAutonomy.get()) {
    if ($effectiveTier.get() === 'quiet') {
      stopRoam()

      if ($spatialLocale.get() !== 'home') {
        setLocale('home')
      }

      return
    }

    return
  }

  const tier = $effectiveTier.get()

  if (tier === 'quiet') {
    stopRoam()

    if ($spatialLocale.get() !== 'home') {
      setLocale('home')
    }

    return
  }

  const ctx = $focusContext.get()
  const canPerch = ctx?.windowGeom && ctx.category !== 'unknown' && ctx.category !== 'gaming' && !ctx.fullscreen

  if (canPerch) {
    stopRoam()

    if ($spatialLocale.get() !== 'perch' && state === 'idle') {
      const perch = computePerchPosition(ctx!.windowGeom!)

      if (perch) {
        setLocale('perch', { position: perch })
      }
    }

    return
  }

  if (tier === 'proactive' && state === 'idle') {
    if ($spatialLocale.get() !== 'roam') {
      startRoam()
    }

    return
  }

  stopRoam()

  if ($spatialLocale.get() === 'perch' || $spatialLocale.get() === 'roam') {
    setLocale('home')
  }
}

let roamTimer: ReturnType<typeof setTimeout> | null = null
let roaming = false

function generateRoamWaypoint(): { x: number; y: number } {
  if (typeof window === 'undefined') {
    return { x: 0, y: 0 }
  }

  const vw = window.innerWidth
  const vh = window.innerHeight

  return {
    x: REST_MARGIN + Math.random() * Math.max(0, vw - SPRITE_W - 2 * REST_MARGIN),
    y: Math.max(REST_MARGIN, vh * 0.5 + Math.random() * Math.max(0, vh * 0.4 - SPRITE_H))
  }
}

export function startRoam(): void {
  if (roaming) {
    return
  }

  roaming = true
  $spatialLocale.set('roam')
  roamStep()
}

function roamStep(): void {
  moveTo(generateRoamWaypoint(), 'walk', () => {
    if (!roaming) {
      return
    }

    roamTimer = setTimeout(
      () => {
        roamTimer = null

        if (roaming && $spriteState.get() === 'idle') {
          roamStep()
        }
      },
      5000 + Math.random() * 10000
    )
  })
}

function stopRoam(): void {
  roaming = false

  if (roamTimer !== null) {
    clearTimeout(roamTimer)
    roamTimer = null
  }

  cancelMovement()
}

export function reevaluateSpatialDecision(): void {
  updateSpatialDecision()
}

export function startDrag(): void {
  userInteracted = true
  stopRoam()
  cancelMovement()
  $spatialLocomotion.set('drag')
  $clipOverride.set('drag')
  $spriteState.set('interacting')
}

export function updateDragPosition(pos: { x: number; y: number }, vel?: { vx: number; vy: number }): void {
  $spatialPos.set(pos)

  if (vel) {
    $dragVelocity.set(vel)
  }
}

export function endDragAt(pos: { x: number; y: number }): void {
  $spatialPos.set(pos)
  $homePosition.set(pos)
  $spatialLocomotion.set('still')
  $spatialLocale.set('home')
  $dragVelocity.set({ vx: 0, vy: 0 })
  $clipOverride.set('drag_end')
  setSpriteState('interacting', { durationMs: 500 })
  void window.spiritagent.sprite.setPosition(pos)
}

export function initSpatial(): () => void {
  void window.spiritagent.sprite
    .getPosition()
    .then(saved => {
      if (!saved || userInteracted) {
        return
      }

      const w = getBaseSpriteWidth()
      const h = getBaseSpriteHeight()

      const next = {
        x: Math.max(REST_MARGIN, Math.min(saved.x, window.innerWidth - w - REST_MARGIN)),
        y: Math.max(REST_MARGIN, Math.min(saved.y, window.innerHeight - h - REST_MARGIN))
      }

      $homePosition.set(next)

      if ($spatialLocale.get() === 'home') {
        $spatialPos.set(next)
      }
    })
    .catch(() => {})

  const unlistenChat = $chatOpen.listen(open => {
    if (open) {
      stopRoam()
      cancelMovement()
      $spatialLocomotion.set('still')
    }
  })

  const unlistenState = $spriteState.listen(() => {
    updateAdaptiveScale()
    updateSpatialDecision()
  })

  const unlistenEmotion = $spriteEmotion.listen(() => updateAdaptiveScale())

  const unlistenTier = $effectiveTier.listen(() => {
    updateAdaptiveScale()
    updateSpatialDecision()
  })

  const unlistenFocus = $focusContext.listen(() => updateSpatialDecision())

  const onResize = () => {
    $viewport.set({ width: window.innerWidth, height: window.innerHeight })

    // 拖拽中切换显示器也会触发 resize，事件携带新显示器的视口——此时若重新
    // 推算 home/locale，会把精灵从光标下抽走。
    if ($spatialLocomotion.get() === 'drag') {
      return
    }

    const home = $homePosition.get()
    const w = getBaseSpriteWidth()
    const h = getBaseSpriteHeight()

    const clamped = {
      x: Math.max(REST_MARGIN, Math.min(home.x, window.innerWidth - w - REST_MARGIN)),
      y: Math.max(REST_MARGIN, Math.min(home.y, window.innerHeight - h - REST_MARGIN))
    }

    $homePosition.set(clamped)

    const locale = $spatialLocale.get()

    if (locale === 'home') {
      setLocale('home', { instant: true })
    }
  }

  window.addEventListener('resize', onResize)

  return () => {
    unlistenChat()
    unlistenState()
    unlistenEmotion()
    unlistenTier()
    unlistenFocus()
    window.removeEventListener('resize', onResize)
    stopRoam()
    cancelMovement()

    if (scaleRafId !== null) {
      cancelAnimationFrame(scaleRafId)
      scaleRafId = null
    }
  }
}
