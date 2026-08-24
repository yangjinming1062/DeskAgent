import { atom } from 'nanostores'

import { $focusContext, $lastIdleSeconds } from '@/companion/activity'
import { $chatOpen } from '@/companion/chat-store'
import {
  $clipOverride,
  $effectiveTier,
  $spriteAction,
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
// roam 的桌面空闲门槛（DESIGN §3.2「桌面空闲 + 高活跃档位时随机游走」）；
// $lastIdleSeconds 为 -1（Runner 离线/未知）时保守视为不空闲。
const ROAM_IDLE_THRESHOLD_SECONDS = 90
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

// Locomotion 枚举（mesh2d 与 spatial 共用）：
// - 'still' / 'walk' / 'fly' / 'drag' 是原有 4 项；
// - 'walk_fast' 是走路加速版（mesh2d 骨骼相位频率更高）；
// - 'jump' 是单次脉冲，mesh2d 走 body_main squash + shoulder 上扬方案；
// - 'fall' 是自由落体姿态。
// 扩展时务必同步更新 backend/services/companion/mesh2d/manifest_exporter.py::DEFAULT_LOCOMOTION。
export type Locomotion = 'still' | 'walk' | 'walk_fast' | 'fly' | 'drag' | 'jump' | 'fall'

export type EdgeDockSide = 'none' | 'left' | 'right'

export const $spatialLocale = atom<SpatialLocale>('home')
export const $spatialPos = atom<{ x: number; y: number }>(getHomePosition())
export const $homePosition = atom<{ x: number; y: number }>(getHomePosition())
export const $defaultScale = atom<number>(readDefaultScale())
export const $spatialScale = atom<number>($defaultScale.get())
export const $spatialLocomotion = atom<Locomotion>('still')
export const $dragVelocity = atom<{ vx: number; vy: number }>({ vx: 0, vy: 0 })
export const $edgeDockSide = atom<EdgeDockSide>('none')
export const $isEdgeDocked = atom<boolean>(false)

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

// 不变量（DESIGN §3.7）：精灵面部始终在屏内。
// 面部约占精灵上 30% 区域；强制约束 sprite 顶部 y ≥ 0 且 face 底 y ≤ vh，
// 保证 face 整段都落在屏幕内——sprite 主体可以部分越过底部（向下延伸 h-faceH），
// 顶/左/右仍受 REST_MARGIN 兜底。贴边吸附（peeking）仅水平方向处理。
export const FACE_TOP_RATIO = 0.3

export function clampPosToViewport(pos: { x: number; y: number }): { x: number; y: number } {
  if (typeof window === 'undefined') {
    return pos
  }

  const w = getBaseSpriteWidth()
  const h = getBaseSpriteHeight()
  const faceH = h * FACE_TOP_RATIO
  const vw = window.innerWidth
  const vh = window.innerHeight

  return {
    x: Math.max(REST_MARGIN, Math.min(vw - w - REST_MARGIN, pos.x)),
    y: Math.max(0, Math.min(vh - faceH, pos.y))
  }
}

export interface PerchPlacement {
  pos: { x: number; y: number }
  scale: number
}

/** 栖息落位（DESIGN §3.3）：窗口右缘优先、左缘次之；两侧放不下全尺寸时等比例缩到
 * 能舒适栖身（不低于 MIN_SCALE），连最小尺寸都容不下才放弃。 */
export function computePerchPlacement(
  geom: { x: number; y: number; w: number; h: number },
  maxScale: number
): PerchPlacement | null {
  if (typeof window === 'undefined') {
    return null
  }

  const margin = 8
  const rightAvail = Math.max(0, window.innerWidth - REST_MARGIN - (geom.x + geom.w) - margin)
  const leftAvail = Math.max(0, geom.x - margin - REST_MARGIN)
  const rightScale = Math.min(maxScale, rightAvail / SPRITE_W)
  const leftScale = Math.min(maxScale, leftAvail / SPRITE_W)

  let side: 'left' | 'right'
  let scale: number

  if (rightScale >= MIN_SCALE && rightScale >= leftScale) {
    side = 'right'
    scale = rightScale
  } else if (leftScale >= MIN_SCALE) {
    side = 'left'
    scale = leftScale
  } else {
    return null
  }

  const spriteH = SPRITE_H * scale
  const x = side === 'right' ? geom.x + geom.w + margin : geom.x - margin - SPRITE_W * scale

  const y = Math.max(
    REST_MARGIN,
    Math.min(geom.y + geom.h - spriteH - margin, window.innerHeight - spriteH - REST_MARGIN)
  )

  return { pos: { x, y }, scale }
}

export function computePerchPosition(geom: {
  x: number
  y: number
  w: number
  h: number
}): { x: number; y: number } | null {
  return computePerchPlacement(geom, 1)?.pos ?? null
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

  cancelPhysics()

  const cb = moveOnArrive
  moveStart = null
  moveTarget = null
  moveOnArrive = null

  if ($spatialLocomotion.get() !== 'drag' && $spatialLocomotion.get() !== 'fall') {
    $spatialLocomotion.set('still')
  }

  cb?.()
}

let scaleRafId: number | null = null
let scaleStartVal = 1
let scaleTargetVal = 1
let scaleStartTime = 0

// 栖息空间不足时的缩身上限（DESIGN §3.3）；仅 perch 场所生效，离开栖息即解除。
let perchScaleLimit: number | null = null

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

  let target = base
  const emotion = $spriteEmotion.get()

  if ($spriteState.get() === 'emotional' && emotion) {
    const factor = EMOTION_SCALE_BOOST[emotion]
    target = factor ? Math.min(base * factor, MAX_SCALE) : base
  }

  // 栖息缩身上限压过情绪放大：空间不够时先保证舒适栖身
  const cap = $spatialLocale.get() === 'perch' ? perchScaleLimit : null

  return cap !== null ? Math.min(target, cap) : target
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

function localePosition(_locale: SpatialLocale): { x: number; y: number } {
  return $homePosition.get()
}

function defaultLocomotion(locale: SpatialLocale): 'walk' | 'fly' | 'still' {
  return locale === 'target' ? 'fly' : 'walk'
}

export function setLocale(
  locale: SpatialLocale,
  opts?: {
    position?: { x: number; y: number }
    locomotion?: 'walk' | 'fly'
    instant?: boolean
    /** perch 专属：空间不足缩身后的缩放上限（DESIGN §3.3）；缺省 = 不限 */
    scaleLimit?: number
    onArrive?: () => void
  }
): void {
  const limitChanged = perchScaleLimit !== (locale === 'perch' ? (opts?.scaleLimit ?? null) : null)
  perchScaleLimit = locale === 'perch' ? (opts?.scaleLimit ?? null) : null
  $spatialLocale.set(locale)

  if (limitChanged) {
    updateAdaptiveScale()
  }

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
      const perch = computePerchPlacement(ctx!.windowGeom!, $defaultScale.get())

      if (perch) {
        setLocale('perch', { position: perch.pos, scaleLimit: perch.scale })
      }
    }

    return
  }

  if (tier === 'proactive' && state === 'idle' && $lastIdleSeconds.get() >= ROAM_IDLE_THRESHOLD_SECONDS) {
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

        if (!roaming) {
          return
        }

        // 桌面不再空闲（用户回来了）→ 结束漫游、走回 home（DESIGN §3.2 roam 仅桌面空闲时）
        if ($spriteState.get() !== 'idle' || $lastIdleSeconds.get() < ROAM_IDLE_THRESHOLD_SECONDS) {
          stopRoam()
          setLocale('home')

          return
        }

        roamStep()
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

let fallRafId: number | null = null
let fallLastTime = 0

export function cancelPhysics(): void {
  if (fallRafId !== null) {
    cancelAnimationFrame(fallRafId)
    fallRafId = null
  }
}

export function dockToEdge(side: 'left' | 'right'): void {
  cancelMovement()
  cancelPhysics()
  const vw = typeof window !== 'undefined' ? window.innerWidth : 1920
  const vh = typeof window !== 'undefined' ? window.innerHeight : 1080
  const spriteW = getBaseSpriteWidth()
  const spriteH = getBaseSpriteHeight()
  const targetY = Math.max(REST_MARGIN, Math.min($spatialPos.get().y, vh - spriteH - REST_MARGIN))

  // 身体约 65% 缩进屏幕边缘，只探出头部/耳朵往里看
  const hiddenAmount = spriteW * 0.65
  const targetX = side === 'left' ? -hiddenAmount : vw - spriteW + hiddenAmount

  $isEdgeDocked.set(true)
  $edgeDockSide.set(side)
  $spatialLocomotion.set('still')
  $clipOverride.set('peeking')
  $spriteAction.set('peeking')

  moveTo({ x: targetX, y: targetY }, 'walk', () => {
    $spatialPos.set({ x: targetX, y: targetY })
    $homePosition.set({ x: targetX, y: targetY })
    void window.spiritagent.sprite.setPosition({ x: targetX, y: targetY })
  })
}

export function undockFromEdge(): void {
  if (!$isEdgeDocked.get()) {
    return
  }

  cancelMovement()
  const side = $edgeDockSide.get()
  const vw = typeof window !== 'undefined' ? window.innerWidth : 1920
  const spriteW = getBaseSpriteWidth()
  const curPos = $spatialPos.get()

  const targetX = side === 'left' ? REST_MARGIN : vw - spriteW - REST_MARGIN
  $isEdgeDocked.set(false)
  $edgeDockSide.set('none')
  $clipOverride.set(null)

  // peeking 是 loop 动作：脱离贴边后清掉，否则探头姿态挂在非贴边语境里
  if ($spriteAction.get() === 'peeking') {
    $spriteAction.set(null)
  }

  moveTo({ x: targetX, y: curPos.y }, 'walk', () => {
    $spatialPos.set({ x: targetX, y: curPos.y })
    $homePosition.set({ x: targetX, y: curPos.y })
    void window.spiritagent.sprite.setPosition({ x: targetX, y: curPos.y })
  })
}

export function startFreeFall(initialPos: { x: number; y: number }, velocity: { vx: number; vy: number }): void {
  cancelMovement()
  cancelPhysics()

  const vw = typeof window !== 'undefined' ? window.innerWidth : 1920
  const vh = typeof window !== 'undefined' ? window.innerHeight : 1080
  const spriteW = getBaseSpriteWidth()
  const spriteH = getBaseSpriteHeight()
  const groundY = Math.max(REST_MARGIN, vh - spriteH - REST_MARGIN)
  const minX = REST_MARGIN
  const maxX = Math.max(REST_MARGIN, vw - spriteW - REST_MARGIN)

  // 速度换算为 px/s（pointer velocity 在 px/ms）
  let vx = (velocity.vx || 0) * 1000
  let vy = (velocity.vy || 0) * 1000

  vx = Math.max(-2500, Math.min(2500, vx))
  vy = Math.max(-2500, Math.min(2500, vy))

  let curX = initialPos.x
  let curY = initialPos.y

  if (curY >= groundY - 6 && Math.abs(vy) < 100) {
    endDragAt({ x: Math.max(minX, Math.min(maxX, curX)), y: groundY })

    return
  }

  $spatialLocomotion.set('fall')
  $clipOverride.set('fall')
  $spriteAction.set('fall')
  setSpriteState('interacting')

  const GRAVITY = 2600 // px/s^2 重力加速度
  const DRAG_X = 0.985
  const RESTITUTION = 0.35 // 触地反弹衰减系数
  fallLastTime = performance.now()

  function physicsStep(now: number) {
    const dt = Math.min(0.04, Math.max(0.001, (now - fallLastTime) / 1000))
    fallLastTime = now

    vy += GRAVITY * dt
    vx *= Math.pow(DRAG_X, dt * 60)

    curX += vx * dt
    curY += vy * dt

    // 左右屏幕墙壁弹性碰撞
    if (curX <= minX) {
      curX = minX
      vx = -vx * 0.4
    } else if (curX >= maxX) {
      curX = maxX
      vx = -vx * 0.4
    }

    // 地面碰撞与挤压反弹
    if (curY >= groundY) {
      curY = groundY

      if (Math.abs(vy) > 160) {
        // 反弹 + 触地挤压
        vy = -vy * RESTITUTION
        $clipOverride.set('land_squash')
        $spriteAction.set('land_squash')
      } else {
        // 稳定触地，物理结算完毕
        cancelPhysics()
        endDragAt({ x: Math.max(minX, Math.min(maxX, curX)), y: groundY })

        return
      }
    }

    $spatialPos.set({ x: curX, y: curY })
    fallRafId = requestAnimationFrame(physicsStep)
  }

  fallRafId = requestAnimationFrame(physicsStep)
}

export function startDrag(): void {
  userInteracted = true
  stopRoam()
  cancelMovement()
  cancelPhysics()

  if ($isEdgeDocked.get()) {
    $isEdgeDocked.set(false)
    $edgeDockSide.set('none')
  }

  $spatialLocomotion.set('drag')
  $clipOverride.set('drag')
  $spriteState.set('interacting')
}

export function updateDragPosition(pos: { x: number; y: number }, vel?: { vx: number; vy: number }): void {
  // DESIGN §3.7：face 始终在屏内。拖拽过程中也必须遵守，不能等 endDragAt 才修正。
  $spatialPos.set(clampPosToViewport(pos))

  if (vel) {
    $dragVelocity.set(vel)
  }
}

export function endDragAt(pos: { x: number; y: number }, vel?: { vx: number; vy: number }): void {
  const vw = typeof window !== 'undefined' ? window.innerWidth : 1920
  const vh = typeof window !== 'undefined' ? window.innerHeight : 1080
  const spriteW = getBaseSpriteWidth()
  const spriteH = getBaseSpriteHeight()
  const dockMargin = 40

  // 1. 优先判定屏幕左右边缘吸附
  if (pos.x <= dockMargin) {
    dockToEdge('left')

    return
  }

  if (pos.x >= vw - spriteW - dockMargin) {
    dockToEdge('right')

    return
  }

  // 2. 判定空中自由落体与初速度抛掷
  const groundY = Math.max(REST_MARGIN, vh - spriteH - REST_MARGIN)

  if (vel && (pos.y < groundY - 12 || Math.hypot(vel.vx, vel.vy) > 0.15)) {
    startFreeFall(pos, vel)

    return
  }

  // 3. 落地静止：把坐标收紧到 viewport 内，并强制 face 不被裁剪（DESIGN §3.7）
  const safe = clampPosToViewport(pos)

  cancelPhysics()
  $isEdgeDocked.set(false)
  $edgeDockSide.set('none')
  $spatialPos.set(safe)
  $homePosition.set(safe)
  $spatialLocomotion.set('still')
  $spatialLocale.set('home')
  $dragVelocity.set({ vx: 0, vy: 0 })
  $clipOverride.set('drag_end')
  $spriteAction.set('drag_end')
  setSpriteState('interacting', { durationMs: 500 })
  void window.spiritagent.sprite.setPosition(safe)
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
