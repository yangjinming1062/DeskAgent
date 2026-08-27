import { atom } from 'nanostores'

import { $focusContext, $lastIdleSeconds } from '@/companion/activity'
import { $chatOpen } from '@/companion/chat-store'
import {
  $clipOverride,
  $effectiveTier,
  $spriteAction,
  $spriteEmotion,
  $spriteState,
  $voiceCallOpen,
  setSpriteState
} from '@/companion/companion-store'
import { $llmAutonomy } from '@/companion/prefs'
import { persistString, storedString } from '@/shared/lib/storage'

export function getBaseSpriteHeight(): number {
  // 默认高度为显示器高度的 1/3，限制在 [260, 960] 区间内
  return Math.round(Math.max(260, Math.min(window.innerHeight / 3, 960)))
}

export function getBaseSpriteWidth(): number {
  return Math.round(getBaseSpriteHeight() * 0.85)
}

const REST_MARGIN = 24

const WALK_SPEED = 80
const FLY_SPEED = 400
// 语音通话面板与精灵刚体一体的锚定几何（与 voice-call-dock 面板实际尺寸保持一致）。
export const VOICE_DOCK_W = 320
export const VOICE_DOCK_H = 348
const VOICE_DOCK_GAP = 12
const VOICE_DOCK_BOTTOM_MARGIN = 16
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

const MIN_SCALE = 0.3
const MAX_SCALE = 3

type SpatialLocale = 'home' | 'perch' | 'target' | 'roam'

// Locomotion 枚举（mesh2d 与 spatial 共用）：
// - 'still' / 'walk' / 'fly' / 'drag' 是原有 4 项；
// - 'walk_fast' 是走路加速版（mesh2d 骨骼相位频率更高）；
// - 'jump' 是单次脉冲，mesh2d 走 body_main squash + shoulder 上扬方案。
export type Locomotion = 'still' | 'walk' | 'walk_fast' | 'fly' | 'drag' | 'jump'

type EdgeDockSide = 'none' | 'left' | 'right'

const $spatialLocale = atom<SpatialLocale>('home')

// 可见内容包围盒（归一化到舞台盒）：角色实际可见像素的范围，由渲染层上报——
// puppet 用 rig 层矩形并集，3D 用轮廓 alpha 外接矩形；蛋等未上报路径按整盒兜底。
// 必须先于下方位置原子声明：home 初值求值期就经 contentBox 读它，晚声明会 TDZ 崩页。
export const $spriteContentRect = atom<{ left: number; top: number; right: number; bottom: number } | null>(null)

export const $defaultScale = atom<number>(readDefaultScale())
export const $spatialPos = atom<{ x: number; y: number }>(getHomePosition())
export const $homePosition = atom<{ x: number; y: number }>(getHomePosition())
export const $spatialScale = atom<number>($defaultScale.get())
export const $spatialLocomotion = atom<Locomotion>('still')
export const $dragVelocity = atom<{ vx: number; vy: number }>({ vx: 0, vy: 0 })
const $edgeDockSide = atom<EdgeDockSide>('none')
export const $isEdgeDocked = atom<boolean>(false)

// 窗口视口尺寸——单一真实源，由 initSpatial 已有的 resize 监听器更新。
// 弹层（chat-dock、proactive 气泡）订阅这里而不是各自挂监听器。
interface ViewportSize {
  width: number
  height: number
}

export const $viewport = atom<ViewportSize>({ width: window.innerWidth, height: window.innerHeight })

function getHomePosition(): { x: number; y: number } {
  // home 是休息落点，scale 恒为用户默认比例（瞬时放大只在互动中发生）；
  // 脚底贴视口底（站在任务栏上沿），右侧留呼吸间距。
  const c = contentBox($defaultScale.get())

  return {
    x: Math.max(REST_MARGIN, window.innerWidth - c.right - REST_MARGIN),
    y: Math.max(-c.top, window.innerHeight - c.bottom)
  }
}

// 不变量（DESIGN §3.7）：精灵全身始终完整在屏内——垂直方向任何时候不裁切身体；
// 唯一的局部隐藏是左右贴边探头（§3.2，仅水平方向缩进屏外）。
// 「全身」按可见像素计：贴边是角色贴边，不是渲染画布贴边——舞台盒四周的透明
// 留白可以越出屏幕。钳制与落位一律用缩放后的可见内容包围盒（见 contentBox）。

// 舞台盒内的可见内容包围盒（缩放后像素）。未上报时按整盒兜底（保守：贴不到边缘）。
function contentBox(scale = $spatialScale.get()): { left: number; top: number; right: number; bottom: number } {
  const r = $spriteContentRect.get()

  return {
    left: (r?.left ?? 0) * getBaseSpriteWidth() * scale,
    top: (r?.top ?? 0) * getBaseSpriteHeight() * scale,
    right: (r?.right ?? 1) * getBaseSpriteWidth() * scale,
    bottom: (r?.bottom ?? 1) * getBaseSpriteHeight() * scale
  }
}

// 通话中精灵位置的 y 上限：精灵脚下（含间距与面板高）必须完整落在屏内，
// 面板才有落脚处——面板恒锚在脚下，与精灵刚体一体。
function voiceMaxSpriteY(): number {
  const spriteH = getBaseSpriteHeight() * $spatialScale.get()

  return window.innerHeight - VOICE_DOCK_BOTTOM_MARGIN - VOICE_DOCK_GAP - VOICE_DOCK_H - spriteH
}

function clampPosToViewport(
  pos: { x: number; y: number },
  voiceConstraint = $voiceCallOpen.get()
): {
  x: number
  y: number
} {
  const c = contentBox()
  const vw = window.innerWidth
  const vh = window.innerHeight
  const maxY = voiceConstraint ? Math.min(vh - c.bottom, voiceMaxSpriteY()) : vh - c.bottom

  return {
    x: Math.max(-c.left, Math.min(vw - c.right, pos.x)),
    y: Math.max(-c.top, Math.min(maxY, pos.y))
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
  const margin = 8
  const spriteW = getBaseSpriteWidth()
  const spriteH0 = getBaseSpriteHeight()
  const rightAvail = Math.max(0, window.innerWidth - REST_MARGIN - (geom.x + geom.w) - margin)
  const leftAvail = Math.max(0, geom.x - margin - REST_MARGIN)
  const rightScale = Math.min(maxScale, rightAvail / spriteW)
  const leftScale = Math.min(maxScale, leftAvail / spriteW)

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

  const spriteH = spriteH0 * scale
  const x = side === 'right' ? geom.x + geom.w + margin : geom.x - margin - spriteW * scale

  const y = Math.max(
    REST_MARGIN,
    Math.min(geom.y + geom.h - spriteH - margin, window.innerHeight - spriteH - REST_MARGIN)
  )

  return { pos: { x, y }, scale }
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
  const spriteW = getBaseSpriteWidth() * scale
  const spriteH = getBaseSpriteHeight() * scale
  const spriteRight = pos.x + spriteW
  const fitsRight = spriteRight + gap + overlayMaxW <= vw

  const left = fitsRight ? spriteRight + gap : Math.max(0, pos.x - gap - overlayMaxW)

  const top = Math.max(
    0,
    overlayH > 0 ? Math.min(vh - overlayH, pos.y + spriteH * verticalRatio) : pos.y + spriteH * verticalRatio
  )

  return { left, top }
}

// 语音通话面板锚点：恒定在精灵脚下水平居中——面板与精灵刚体一体，拖动任一者整体平移。
// 脚下空间由开启通话时的上提（ensureVoiceDockRoom）与拖拽钳制保证；
// 窗口 resize、情绪放大等瞬时可越界，这里保留视口钳制兜底（宁可叠上精灵也不能沉出屏外）。
export function computeVoiceCallDockPosition(
  pos: { x: number; y: number },
  scale: number
): {
  left: number
  top: number
} {
  const spriteW = getBaseSpriteWidth() * scale
  const spriteH = getBaseSpriteHeight() * scale
  const vw = window.innerWidth
  const vh = window.innerHeight

  const left = Math.max(
    VOICE_DOCK_BOTTOM_MARGIN,
    Math.min(vw - VOICE_DOCK_W - VOICE_DOCK_BOTTOM_MARGIN, pos.x + (spriteW - VOICE_DOCK_W) / 2)
  )

  const top = Math.min(vh - VOICE_DOCK_H - VOICE_DOCK_BOTTOM_MARGIN, pos.y + spriteH + VOICE_DOCK_GAP)

  return { left: Math.round(left), top: Math.round(top) }
}

// 通话开启时脚下放不下面板则上提让位，挂断后回落原位。上提/回落是瞬时跳变——
// 面板的出现与消失本身没有移动过程，刚体一体不允许二者错拍（精灵还在飞、面板已就位）。
// 用户在通话中拖动（精灵或面板）即视为接管位置，回落取消。
let voiceLiftReturn: { x: number; y: number } | null = null

export function ensureVoiceDockRoom(): void {
  stopRoam()

  // 贴边探头（身体缩进屏外）与脚下锚定的通话面板互斥——先全身回屏。
  if ($isEdgeDocked.get()) {
    undockFromEdge()
  }

  const pos = $spatialPos.get()
  // 极矮视口下 maxY 可为负——钳到 0（面部贴屏顶），面板交给锚点函数的兜底钳制。
  const maxY = Math.max(0, voiceMaxSpriteY())

  if (pos.y <= maxY) {
    return
  }

  voiceLiftReturn = pos

  // x 钳制吸收贴边回屏尚未走完的越界横坐标。
  const vw = window.innerWidth
  const w = getBaseSpriteWidth() * $spatialScale.get()

  const lifted = {
    x: Math.max(REST_MARGIN, Math.min(vw - w - REST_MARGIN, pos.x)),
    y: maxY
  }

  cancelMovement()
  $spatialPos.set(lifted)
  $spatialLocomotion.set('still')
  $homePosition.set(lifted)
  void window.spiritagent.sprite.setPosition(lifted)
}

export function releaseVoiceDockRoom(): void {
  const back = voiceLiftReturn
  voiceLiftReturn = null

  if (!back) {
    return
  }

  // 挂断时 $voiceCallOpen 尚未翻回 false（子组件 cleanup 先于父级 effect），
  // 显式按无通话约束钳制，否则回落目标会被脚下钳制按住不放。
  const safe = clampPosToViewport(back, false)

  cancelMovement()
  $spatialPos.set(safe)
  $spatialLocomotion.set('still')
  $homePosition.set(safe)
  void window.spiritagent.sprite.setPosition(safe)
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

function setScaleTarget(scale: number, instant = false): void {
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

  if ($effectiveTier.get() === 'still') {
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

  // home 落点可能记录于更低 scale 的时期；按当前 scale 重钳，情绪放大期间回 home 不裁脚。
  const rawTarget = opts?.position ?? $homePosition.get()
  const target = locale === 'home' ? clampPosToViewport(rawTarget) : rawTarget
  const locomotion = opts?.locomotion ?? (locale === 'target' ? 'fly' : 'walk')

  if (opts?.instant) {
    cancelMovement()
    $spatialPos.set(target)
    $spatialLocomotion.set('still')
    opts?.onArrive?.()
  } else {
    moveTo(target, locomotion, opts?.onArrive)
  }
}

let userInteracted = false

export function updateSpatialDecision(): void {
  // 通话中面板锚定精灵脚下，精灵必须留原位（DESIGN §6.1）——与 chat 同样冻结空间决策。
  if ($spatialLocomotion.get() === 'drag' || $chatOpen.get() || $voiceCallOpen.get()) {
    return
  }

  const state = $spriteState.get()
  const tier = $effectiveTier.get()

  // 静止档的硬约束优先于一切——这是用户偏好，LLM 自主模式也没有上下文可以参考。
  if (tier === 'still') {
    stopRoam()

    if ($spatialLocale.get() !== 'home') {
      setLocale('home')
    }

    return
  }

  // 常规档不发起任何自动移动——停在原地，只停掉进行中的漫游（DESIGN §3.5）。
  if (tier !== 'autonomous') {
    stopRoam()

    return
  }

  // 自主档下 LLM 自主模式负责 perch/roam/home 的切换；本地规则不再决策。
  if ($llmAutonomy.get()) {
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

  if (state === 'idle' && $lastIdleSeconds.get() >= ROAM_IDLE_THRESHOLD_SECONDS) {
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
  const vw = window.innerWidth
  const vh = window.innerHeight
  const w = getBaseSpriteWidth() * $spatialScale.get()
  const h = getBaseSpriteHeight() * $spatialScale.get()

  return {
    x: REST_MARGIN + Math.random() * Math.max(0, vw - w - 2 * REST_MARGIN),
    y: Math.max(REST_MARGIN, vh * 0.5 + Math.random() * Math.max(0, vh * 0.4 - h))
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

function dockToEdge(side: 'left' | 'right'): void {
  cancelMovement()
  const vw = window.innerWidth
  const spriteW = getBaseSpriteWidth() * $spatialScale.get()
  const c = contentBox()
  const targetY = Math.max(-c.top, Math.min($spatialPos.get().y, window.innerHeight - c.bottom))

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
  const vw = window.innerWidth
  const spriteW = getBaseSpriteWidth() * $spatialScale.get()
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

export function startDrag(): void {
  userInteracted = true
  // 用户拖动即接管位置：挂断后的自动回落作废。
  voiceLiftReturn = null
  stopRoam()
  cancelMovement()

  if ($isEdgeDocked.get()) {
    $isEdgeDocked.set(false)
    $edgeDockSide.set('none')
  }

  $spatialLocomotion.set('drag')
  $clipOverride.set('drag')
  $spriteState.set('interacting')
}

export function updateDragPosition(pos: { x: number; y: number }, vel?: { vx: number; vy: number }): void {
  // DESIGN §3.7：全身始终在屏内。拖拽过程中逐帧钳制，不能等 endDragAt 才修正。
  $spatialPos.set(clampPosToViewport(pos))

  if (vel) {
    $dragVelocity.set(vel)
  }
}

export function endDragAt(pos: { x: number; y: number }): void {
  const vw = window.innerWidth
  const spriteW = getBaseSpriteWidth() * $spatialScale.get()
  const dockMargin = 40

  // 1. 优先判定屏幕左右边缘吸附（通话中面板锚定脚下，禁止贴边探头把面板甩到脚边）
  if (!$voiceCallOpen.get()) {
    if (pos.x <= dockMargin) {
      dockToEdge('left')

      return
    }

    if (pos.x >= vw - spriteW - dockMargin) {
      dockToEdge('right')

      return
    }
  }

  // 2. 松手定居：把坐标收紧到 viewport 内，全身完整入屏（DESIGN §3.7）
  const safe = clampPosToViewport(pos)

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

      const next = clampPosToViewport(saved, false)

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

  // 情绪瞬时放大等 scale 变化不得让已落位的精灵溢出视口（DESIGN §3.7 全身在屏）。
  // 拖拽中由逐帧钳制兜底；移动动画中的插值点恒在两端点之间，端点已界内，无需钳。
  const unlistenScale = $spatialScale.listen(() => {
    if ($spatialLocomotion.get() === 'drag' || rafId !== null) {
      return
    }

    const cur = $spatialPos.get()
    const next = clampPosToViewport(cur)

    // 贴边探头的 x 是故意越界的，只收紧 y。
    $spatialPos.set($isEdgeDocked.get() ? { ...cur, y: next.y } : next)
  })

  // 渲染层装配/模型加载完成后才上报内容包围盒——启动期按新盒重贴 home 与当前位
  // （脚从画布底落到角色脚底）。用户已拖拽过则位置属用户意志，不自动迁移。
  const unlistenContent = $spriteContentRect.listen(() => {
    if (userInteracted) {
      return
    }

    const next = clampPosToViewport($homePosition.get())

    $homePosition.set(next)

    if ($spatialLocale.get() === 'home' && $spatialLocomotion.get() !== 'drag') {
      cancelMovement()
      $spatialPos.set(next)
    }
  })

  const onResize = () => {
    $viewport.set({ width: window.innerWidth, height: window.innerHeight })

    // 拖拽中切换显示器也会触发 resize，事件携带新显示器的视口——此时若重新
    // 推算 home/locale，会把精灵从光标下抽走。
    if ($spatialLocomotion.get() === 'drag') {
      return
    }

    const home = $homePosition.get()
    const c = contentBox()

    const clamped = {
      x: Math.max(REST_MARGIN, Math.min(home.x, window.innerWidth - c.right - REST_MARGIN)),
      y: Math.max(-c.top, Math.min(home.y, window.innerHeight - c.bottom))
    }

    $homePosition.set(clamped)

    // 通话中面板锚在脚下：只把当前位置收紧进新视口（脚下约束随新视口重算），
    // 不做 home 重贴——避免把上提中的精灵拉回底部 home。
    if ($voiceCallOpen.get()) {
      $spatialPos.set(clampPosToViewport($spatialPos.get()))

      return
    }

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
    unlistenScale()
    unlistenContent()
    window.removeEventListener('resize', onResize)
    stopRoam()
    cancelMovement()

    if (scaleRafId !== null) {
      cancelAnimationFrame(scaleRafId)
      scaleRafId = null
    }
  }
}
