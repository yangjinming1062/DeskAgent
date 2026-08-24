/** Mesh2D 动作 / locomotion / idle pose 调度器。
 *
 * 设计要点（DESIGN.md §2.3 + mesh2d README）：
 * - 订阅 $spriteState / $spriteEmotion / $spriteAction 三个 atom；
 * - 内部维护四层叠加：Active Action > Locomotion > Idle Variant > Base Micro-motion；
 * - 所有 bone.rotation 单位均为弧度（与 Three.js 一致），写入前由 clampBoneTransform 兜底；
 * - triggerImpulse() 调 createJiggleState 给 hair/skirt/bust 注入 spring-damper 抖动；
 * - 骨骼 transform 红线从 manifest.animations.red_lines 读取（Python 端权威），不硬编码；
 * - 交叉淡化（DESIGN §2.2「约 250ms 量级」）：当前帧以 per-bone strength ramp
 *   （blend_in / blend_out）写入，未采用 3D Mixer 那样的全局 Stage crossfade——
 *   原因：3D 用 Animator 状态机切 clip；2D 的多 pose 同时叠加在同一个 Skeleton 上，
 *   全局 crossfade 会把 idle 微动也一起淡掉。per-bone 强度既满足「无硬切」又不破坏
 *   持续微动层。
 */

import type * as THREE from 'three'

import { $spriteAction, $spriteEmotion, $spriteState } from '@/companion/companion-store'
import type { Locomotion } from '@/companion/spatial'

import type { JiggleConfig, JiggleState } from './mesh2d-bones'
import { createJiggleState } from './mesh2d-bones'
import type { Mesh2DScene } from './mesh2d-runtime'

// ---------------------------------------------------------------------------
// Manifest 子集类型（与 backend/services/companion/mesh2d/manifest_exporter.py 对齐）
// ---------------------------------------------------------------------------

export interface BoneTransform {
  rotation_rad?: { x?: number; y?: number; z?: number }
  scale?: { x?: number; y?: number; z?: number }
  position_offset?: { x?: number; y?: number }
}

export interface ActionDef {
  duration_ms: number
  blend_in_ms: number
  blend_out_ms: number
  loop: boolean
  bones: Record<string, BoneTransform>
}

export interface LocomotionBonePhase {
  amplitude_rad: number
  period_ms: number
  phase_offset: number
  axis: 'x' | 'y' | 'z'
}

export interface LocomotionScaleBob {
  amplitude_scale: number
  period_ms: number
  phase_offset: number
}

export interface LocomotionImpulsePulse {
  magnitude: number
  period_ms: number
}

export interface LocomotionJumpPulse {
  bone: string
  preload_ms: number
  hold_ms: number
  recover_ms: number
  scale_y_min: number
  shoulder_lift_rad: number
}

export interface LocomotionDef {
  bones: Record<string, LocomotionBonePhase | LocomotionScaleBob | LocomotionImpulsePulse>
  pulse?: LocomotionJumpPulse
}

export type LocomotionTable = Record<Locomotion | 'still', LocomotionDef>

export interface Mesh2DAnimations {
  breath: { amplitude: number; period_ms: number }
  blink: { min_period_ms: number; max_period_ms: number; duration_ms: number }
  idle_sway: { amplitude: number; min_period_ms: number; max_period_ms: number }
  jiggle: Record<string, JiggleConfig>
  // 骨骼 transform 红线，从 manifest.animations.red_lines 读取。
  red_lines: Record<string, { rot_max: number; scale_y_max?: number }>
  actions: Record<string, ActionDef>
  idle_variants: string[]
  locomotion: LocomotionTable
}

const TWO_PI = Math.PI * 2

function clampBoneTransform(bone: THREE.Bone, lines: Mesh2DAnimations['red_lines'][string] | undefined): void {
  if (!lines) {
    return
  }

  const rotMax = lines.rot_max

  if (rotMax !== undefined) {
    bone.rotation.x = Math.max(-rotMax, Math.min(rotMax, bone.rotation.x))
    bone.rotation.y = Math.max(-rotMax, Math.min(rotMax, bone.rotation.y))
    bone.rotation.z = Math.max(-rotMax, Math.min(rotMax, bone.rotation.z))
  }

  const scaleYMax = lines.scale_y_max

  if (scaleYMax !== undefined) {
    bone.scale.y = Math.min(scaleYMax, bone.scale.y)
  }
}

// ---------------------------------------------------------------------------
// Mesh2DDriver
// ---------------------------------------------------------------------------

export interface Mesh2DDriverOptions {
  /** idle 变体切换间隔下限（ms）；上限是 2x 下限 */
  idleMinIntervalMs?: number
  /** idle variant 加权采样表（key → weight）；缺省时各 variant 等权重 */
  idleWeights?: Record<string, number>
}

interface ActiveActionState {
  name: string
  def: ActionDef
  startedAt: number // 触发时刻（performance.now()）
  finishedAt: number // blend_out 结束时刻
}

export class Mesh2DDriver {
  private scene: Mesh2DScene
  private actions: Record<string, ActionDef>
  private idleVariants: string[]
  private locomotion: LocomotionTable
  private redLines: Mesh2DAnimations['red_lines']
  private idleMinIntervalMs: number
  private idleWeights: Record<string, number>

  // 当前 active action 状态；null = 当前帧不在播任何动作
  private activeAction: ActiveActionState | null = null
  // 上一次见到 $spriteAction 的值；变化时清零驱动新一轮（即使 key 相同也强制重新触发）
  private lastActionKey: string | null = null
  // idle variant 调度：当前 variant 与下次切换时间
  private currentIdleVariant: string | null = null
  private nextIdleSwapAt = 0

  // locomotion 周期相位时间戳（用于 sin 输入）
  private locomotionPhaseStart = 0
  // 上一次 impulse 触发时间（skirt / back_hair 在 walk 期间按步频周期触发）
  private lastSkirtImpulseAt = 0
  private lastHairImpulseAt = 0
  // jump 脉冲状态
  private jumpStartedAt = 0

  // 用于驱动层写入后还原 base rotation / scale 的"目标 pose"
  // 关键：base pose 写入后再叠加 micro-motion（breath/blink/sway）；所以 base 留一份即可
  private jiggleStates: Map<string, JiggleState>

  // 订阅清理
  private unsubSpriteState: () => void
  private unsubEmotion: () => void
  private unsubAction: () => void

  constructor(scene: Mesh2DScene, animations: Mesh2DAnimations, opts: Mesh2DDriverOptions = {}) {
    this.scene = scene
    this.actions = animations.actions
    this.idleVariants = animations.idle_variants
    this.locomotion = animations.locomotion
    this.redLines = animations.red_lines
    this.idleMinIntervalMs = opts.idleMinIntervalMs ?? 4000
    this.idleWeights = opts.idleWeights ?? {}

    this.jiggleStates = scene.jiggleStates

    // sprite state 变化不在这里处理——frame loop 在 tickAction / tickIdleVariant 内按优先级
    // 体现；这里不强制中断 active action（DESIGN §2.3）。
    this.unsubSpriteState = $spriteState.listen(() => {})
    this.unsubEmotion = $spriteEmotion.listen(() => {})
    this.unsubAction = $spriteAction.listen(action => {
      this.handleActionChange(action)
    })
  }

  /** 重新应用 idle 权重（persona-retune 等场景）；未命中的 variant 保持当前权重。 */
  public setIdleWeights(weights: Record<string, number>): void {
    this.idleWeights = { ...this.idleWeights, ...weights }
  }

  public getIdleVariants(): readonly string[] {
    return this.idleVariants
  }

  /** 每帧调用一次；由 Mesh2DCanvas 在 tickMesh2D 之前调。 */
  tick(now: number, dt: number, locomotion: Locomotion): void {
    this.tickAction(now)

    const inActiveAction = this.activeAction !== null

    if (!inActiveAction) {
      this.tickIdleVariant(now)
    }

    // active action 与 locomotion 共存：pose 表只写动作涉及的骨骼，未占用的骨骼由 locomotion 公式叠加。
    this.tickLocomotion(now, dt, locomotion, inActiveAction)
  }

  /** 把 driver 写入的"base pose"快照到 bone.userData，供 mesh2d-runtime 的 micro-motion 层读取。
   * 必须在 driver.tick() 之后、tickMesh2D() 之前调用。
   */
  cacheBasePose(): void {
    for (const bone of this.scene.bones.values()) {
      if (bone.name === 'head' || bone.name === 'body_main' || bone.name === 'neck') {
        bone.userData.baseRot = {
          x: bone.rotation.x,
          y: bone.rotation.y,
          z: bone.rotation.z
        }
      }

      if (bone.name === 'body_main' || bone.name === 'head') {
        bone.userData.baseScaleY = bone.scale.y
      }
    }
  }

  public getActiveActionName(): string | null {
    return this.activeAction?.name ?? null
  }

  /** LLM 或交互触发的新 action（含 emotion → action 默认映射）。 */
  private handleActionChange(actionKey: string | null): void {
    if (!actionKey) {
      this.lastActionKey = null

      return
    }

    if (actionKey === this.lastActionKey) {
      // 心跳式轮询：强制重新触发（仅对非 looping 动作有意义）
    }

    const def = this.actions[actionKey]

    if (!def) {
      // 未注册的 key 忽略，避免引入任意姿态
      this.lastActionKey = actionKey

      return
    }

    this.lastActionKey = actionKey
    this.beginAction(actionKey, def)
  }

  private beginAction(name: string, def: ActionDef): void {
    const now = performance.now()
    const totalMs = def.blend_in_ms + def.duration_ms + def.blend_out_ms
    this.activeAction = {
      name,
      def,
      startedAt: now,
      finishedAt: now + totalMs
    }
    // active action 期间暂停 idle 计时器；等待完整生命周期（blend_in + duration + blend_out）+ 500ms buffer
    this.nextIdleSwapAt = now + totalMs + 500

    // 触地挤压（DESIGN §3.3）→ 同步向 hair 与 skirt 注入冲击抖动
    if (name === 'land_squash') {
      this.triggerImpulse('skirt', 2.4)
      this.triggerImpulse('back_hair', 1.8)
    }
  }

  private tickAction(now: number): void {
    const a = this.activeAction

    if (!a) {
      return
    }

    const elapsed = now - a.startedAt
    const { duration_ms, blend_in_ms, blend_out_ms, bones } = a.def

    let strength: number

    if (elapsed < blend_in_ms) {
      strength = elapsed / Math.max(blend_in_ms, 1)
    } else if (elapsed < blend_in_ms + duration_ms) {
      strength = 1
    } else if (elapsed < blend_in_ms + duration_ms + blend_out_ms) {
      const t = (elapsed - blend_in_ms - duration_ms) / Math.max(blend_out_ms, 1)
      strength = Math.max(0, 1 - t)
    } else {
      this.activeAction = null

      return
    }

    // 注意：scale 由 base pose / breath / locomotion 复合，不在此写入。
    for (const [boneName, tx] of Object.entries(bones)) {
      const bone = this.scene.bones.get(boneName)

      if (!bone) {
        continue
      }

      if (tx.rotation_rad) {
        if (tx.rotation_rad.x !== undefined) {
          bone.rotation.x = tx.rotation_rad.x * strength
        }

        if (tx.rotation_rad.y !== undefined) {
          bone.rotation.y = tx.rotation_rad.y * strength
        }

        if (tx.rotation_rad.z !== undefined) {
          bone.rotation.z = tx.rotation_rad.z * strength
        }
      }

      clampBoneTransform(bone, this.redLines[bone.name])
    }
  }

  private tickIdleVariant(now: number): void {
    if (this.idleVariants.length === 0) {
      return
    }

    // 仅在 spriteState=idle 时调度 idle variant；其他状态走 LLM action 通道。
    if ($spriteState.get() !== 'idle') {
      return
    }

    const swapDue = !this.currentIdleVariant || now >= this.nextIdleSwapAt

    if (!swapDue) {
      return
    }

    this.currentIdleVariant = this.pickIdleVariant()
    const def = this.actions[this.currentIdleVariant]

    if (def) {
      this.activeAction = {
        name: this.currentIdleVariant,
        def,
        startedAt: now,
        finishedAt: now + def.blend_in_ms + def.duration_ms + def.blend_out_ms
      }
    }

    this.nextIdleSwapAt = now + this.idleMinIntervalMs + Math.random() * this.idleMinIntervalMs
  }

  private pickIdleVariant(): string {
    if (this.idleVariants.length === 0) {
      return 'idle_breath'
    }

    const weights = this.idleVariants.map(name => this.idleWeights[name] ?? 1)
    const total = weights.reduce((s, w) => s + w, 0)
    let pick = Math.random() * total

    for (let i = 0; i < this.idleVariants.length; i++) {
      pick -= weights[i] ?? 1

      if (pick <= 0) {
        return this.idleVariants[i] ?? 'idle_breath'
      }
    }

    return this.idleVariants[0] ?? 'idle_breath'
  }

  private actionBoneNames(now: number): Set<string> {
    if (!this.activeAction) {
      return new Set()
    }

    const elapsed = now - this.activeAction.startedAt
    const { blend_in_ms, duration_ms, blend_out_ms } = this.activeAction.def

    if (elapsed >= blend_in_ms + duration_ms + blend_out_ms) {
      return new Set()
    }

    return new Set(Object.keys(this.activeAction.def.bones))
  }

  private tickLocomotion(now: number, dt: number, locomotion: Locomotion, inActiveAction: boolean): void {
    const def = this.locomotion[locomotion]

    if (!def) {
      return
    }

    if (locomotion === 'jump') {
      this.tickJumpPulse(now, def.pulse)

      return
    }

    if (!def.bones || Object.keys(def.bones).length === 0) {
      return // still / fly / drag：无额外骨骼摆动
    }

    const reserved = inActiveAction ? this.actionBoneNames(now) : new Set<string>()
    const elapsedSec = (now - this.locomotionPhaseStart) / 1000

    for (const [boneName, formula] of Object.entries(def.bones)) {
      // active action 期间（DESIGN §2.3）：暂停所有 *_impulse 与 __scale_y_bob 公式的相位叠加，
      // 避免与动作姿态互相抢占产生抖动。周期骨骼摆动已通过 reserved 集合跳过。
      if (inActiveAction && (boneName.endsWith('_impulse') || boneName.endsWith('__scale_y_bob'))) {
        continue
      }

      if (boneName.endsWith('_impulse')) {
        this.tickLocomotionImpulse(now, boneName, formula as LocomotionImpulsePulse)

        continue
      }

      if (boneName.endsWith('__scale_y_bob')) {
        const f = formula as LocomotionScaleBob
        const phase = (TWO_PI * (elapsedSec * 1000)) / f.period_ms + f.phase_offset
        const bone = this.scene.bones.get('body_main')

        if (bone) {
          bone.scale.y = 1 + Math.abs(Math.sin(phase)) * f.amplitude_scale
          clampBoneTransform(bone, this.redLines[bone.name])
        }

        continue
      }

      const f = formula as LocomotionBonePhase

      if (reserved.has(boneName)) {
        continue
      }

      const bone = this.scene.bones.get(boneName)

      if (!bone) {
        continue
      }

      const phase = (TWO_PI * (elapsedSec * 1000)) / f.period_ms + f.phase_offset
      const value = Math.sin(phase) * f.amplitude_rad

      switch (f.axis) {
        case 'x':
          bone.rotation.x = value

          break

        case 'y':
          bone.rotation.y = value

          break

        case 'z':
          bone.rotation.z = value

          break
      }

      clampBoneTransform(bone, this.redLines[bone.name])
    }
  }

  private tickLocomotionImpulse(now: number, boneName: string, formula: LocomotionImpulsePulse): void {
    const lastRef = boneName === 'skirt_impulse' ? this.lastSkirtImpulseAt : this.lastHairImpulseAt
    const targetBone = boneName === 'skirt_impulse' ? 'skirt' : 'back_hair'

    if (now - lastRef < formula.period_ms) {
      return
    }

    if (boneName === 'skirt_impulse') {
      this.lastSkirtImpulseAt = now
    } else {
      this.lastHairImpulseAt = now
    }

    this.triggerImpulse(targetBone, formula.magnitude)
  }

  private tickJumpPulse(now: number, pulse: LocomotionJumpPulse | undefined): void {
    if (!pulse) {
      return
    }

    if (this.jumpStartedAt === 0) {
      this.jumpStartedAt = now
    }

    const elapsed = now - this.jumpStartedAt
    const { preload_ms, hold_ms, recover_ms, scale_y_min, shoulder_lift_rad } = pulse
    const total = preload_ms + hold_ms + recover_ms

    if (elapsed >= total) {
      this.jumpStartedAt = 0

      return
    }

    const body = this.scene.bones.get(pulse.bone)
    const shoulderL = this.scene.bones.get('shoulder_L')
    const shoulderR = this.scene.bones.get('shoulder_R')

    let bodyScaleY = 1

    if (elapsed < preload_ms) {
      const t = elapsed / preload_ms
      bodyScaleY = 1 + (scale_y_min - 1) * t
    } else if (elapsed < preload_ms + hold_ms) {
      bodyScaleY = scale_y_min
    } else {
      const t = (elapsed - preload_ms - hold_ms) / recover_ms
      bodyScaleY = scale_y_min + (1 - scale_y_min) * t
    }

    if (body) {
      body.scale.y = bodyScaleY
      clampBoneTransform(body, this.redLines[body.name])
    }

    if (shoulderL) {
      shoulderL.rotation.z = shoulder_lift_rad
      clampBoneTransform(shoulderL, this.redLines[shoulderL.name])
    }

    if (shoulderR) {
      shoulderR.rotation.z = -shoulder_lift_rad
      clampBoneTransform(shoulderR, this.redLines[shoulderR.name])
    }
  }

  /** 外部触发 impulse（hover hair / click head 等）。写入 jiggleStates。 */
  triggerImpulse(boneName: string, magnitude: number): void {
    // jiggle 配置 key 约定：skirt_root / hair_back_root / bust；骨骼名是 skirt / back_hair / bust。
    const jiggleKey =
      boneName === 'skirt'
        ? 'skirt_root'
        : boneName === 'back_hair'
          ? 'hair_back_root'
          : boneName === 'front_hair'
            ? 'front_hair_root'
            : boneName

    let state = this.jiggleStates.get(jiggleKey)

    if (!state) {
      state = createJiggleState()
      this.jiggleStates.set(jiggleKey, state)
    }

    state.target = magnitude
    state.velocity = magnitude * 0.6 // 初速度让弹簧立刻有反应
  }

  /** 注销订阅。Mesh2DCanvas 卸载时调用。 */
  dispose(): void {
    this.unsubSpriteState()
    this.unsubEmotion()
    this.unsubAction()
  }
}
