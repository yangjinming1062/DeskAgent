import { atom, computed } from 'nanostores'

import { persistString, storedString } from '@/shared/lib/storage'

// 伙伴生命周期决定精灵窗口渲染的内容。渲染层按
// unauthed → onboarding（向导进行中）→ ready（向导完成后）流转。
export type CompanionLifecycle = 'unauthed' | 'onboarding' | 'ready'

// 第二阶段状态机（plan.md §2）：
// IDLE / LISTENING / THINKING / SPEAKING / WORKING / EMOTIONAL / INTERACTING / DISCONNECTED
export type SpriteStateName =
  | 'idle'
  | 'listening'
  | 'thinking'
  | 'speaking'
  | 'working'
  | 'emotional'
  | 'interacting'
  | 'disconnected'

export type SpriteEmotion = string

export const BUILTIN_EMOTIONS: ReadonlySet<string> = new Set([
  'happy',
  'sad',
  'surprised',
  'excited',
  'confused',
  'concerned',
  'shy',
  'proud',
  'grateful',
  'playful',
  'bored',
  'lonely',
  'sleepy',
  'curious',
  'embarrassed',
  'apologetic',
  'pout',
  'angry',
  'smug',
  'scared',
  'relieved'
])

export const $companionLifecycle = atom<CompanionLifecycle>('unauthed')
export const $spriteState = atom<SpriteStateName>('idle')
// 通话进行中。
export const $voiceCallOpen = atom<boolean>(false)
export const $spriteEmotion = atom<SpriteEmotion | null>(null)
// 可选的结构化动作提示（如 turn_away），用于细化情绪片段。
export const $spriteAction = atom<string | null>(null)
// 动作序列编排队列：$spriteAction 恒为当前/首个动作（3D 只消费单值），后续由 2D driver 逐个推进。
export const $spriteActionQueue = atom<string[]>([])
export const $previousState = atom<SpriteStateName>('idle')
export const $clipOverride = atom<string | null>(null)

// 打扰档位门控伙伴的主动行为（ARCHITECTURE.md §6 / plan.md §4.2）。
// 用户主动行为永不被门控——只门控主动外发（companion.message）。
// `quiet` 屏蔽主动消息，但保持 affect 通道开启（第二阶段）。
//
// 双 atom 模型：
// - ``$userPreferredTier`` ——用户在设置界面手动选择的档位，
//   持久化到 localStorage，作为唯一真源。活动监视器在决定是否覆盖时读取它。
// - ``$effectiveTierOverride`` ——活动监视器在用户处于沉浸式 / 全屏专注上下文时设置。
//   ``null`` 表示「无覆盖；生效值 = 用户偏好」。
// - ``$effectiveTier`` ——由以上两个推导而来。渲染层其余部分读取它来
//   决定是否门控主动通道；settings-overlay / chat-dock 上的标签仍展示
//   user_preferred，反映用户实际选择而非瞬时覆盖。
export type DisturbanceTier = 'proactive' | 'normal' | 'quiet'

// 将所选档位持久化到 localStorage，避免 Desktop 重启时悄悄把用户
// 重置为更聒噪的默认档。后端有自己进程内的缓存
// （services/companion/disturbance.py），但桌面端才是真源——
// 每次变更及网关开启时都把档位回传给后端。
const _rawTier = typeof window === 'undefined' ? null : storedString('da.companion.disturbanceTier')

const _validStored: DisturbanceTier | null =
  _rawTier === 'proactive' || _rawTier === 'normal' || _rawTier === 'quiet' ? (_rawTier as DisturbanceTier) : null

export const $userPreferredTier = atom<DisturbanceTier>(_validStored ?? 'normal')
// ``null`` 表示「当前无覆盖；生效档位回退到 user_preferred」。
// 只有活动监视器（activity.ts）会写它。
export const $effectiveTierOverride = atom<DisturbanceTier | null>(null)

// 手动安静是硬锁定：即便活动监视器在用户已选安静时写入 override，
// 渲染出的生效档位也保持安静。其他覆盖（proactive / normal）
// 仅在用户未选安静时生效。
export const $effectiveTier = computed([$userPreferredTier, $effectiveTierOverride], (preferred, override) =>
  preferred === 'quiet' ? 'quiet' : (override ?? preferred)
)

const STATE_PRIORITY: Record<SpriteStateName, number> = {
  disconnected: 100,
  interacting: 80,
  working: 70,
  speaking: 60,
  thinking: 50,
  listening: 40,
  emotional: 35,
  idle: 10
}

// 这些状态通过 ``$previousState`` 与下方计时器自动恢复。
// 它们绕过优先级门控，避免进行中的 WORKING/SPEAKING 动画
// 压制一个瞬时的情绪/互动提示——新增瞬时状态只需要在这里加一项，
// 而不必改三个代码位置。
const TRANSIENT_STATES: ReadonlySet<SpriteStateName> = new Set(['emotional', 'interacting'])

let transientTimer: ReturnType<typeof setTimeout> | null = null

export function setCompanionLifecycle(next: CompanionLifecycle): void {
  $companionLifecycle.set(next)
}

export function setSpriteState(
  name: SpriteStateName,
  options?: { emotion?: SpriteEmotion; action?: string | null; durationMs?: number; force?: boolean }
): void {
  const current = $spriteState.get()

  if (
    !options?.force &&
    STATE_PRIORITY[name] < STATE_PRIORITY[current] &&
    current !== 'idle' &&
    !TRANSIENT_STATES.has(name)
  ) {
    // 低优先级状态无法打断高优先级状态——瞬时状态除外，
    // 它们会通过下方计时器自动恢复。
    return
  }

  if (options?.force) {
    if (transientTimer) {
      clearTimeout(transientTimer)
      transientTimer = null
    }
  }

  if (TRANSIENT_STATES.has(name)) {
    if (current !== 'emotional' && current !== 'interacting') {
      $previousState.set(current)
    }

    if (options?.emotion) {
      $spriteEmotion.set(options.emotion)
      $spriteAction.set(options.action ?? null)
    }

    $spriteState.set(name)

    if (transientTimer) {
      clearTimeout(transientTimer)
    }

    const ms = options?.durationMs ?? (name === 'emotional' ? 2500 : 1800)
    transientTimer = setTimeout(() => {
      transientTimer = null
      $spriteEmotion.set(null)
      $spriteAction.set(null)
      $spriteActionQueue.set([])
      // 若瞬时过程中有更高优先级状态到达，优先取当前状态。
      const currentAfter = $spriteState.get()
      const storedPrev = $previousState.get()

      const target =
        currentAfter !== 'emotional' && currentAfter !== 'interacting'
          ? currentAfter
          : storedPrev === 'emotional' || storedPrev === 'interacting'
            ? 'idle'
            : storedPrev

      $spriteState.set(target)
    }, ms)

    return
  }

  if (transientTimer) {
    clearTimeout(transientTimer)
    transientTimer = null
  }

  $spriteEmotion.set(options?.emotion ?? null)
  $spriteAction.set(options?.action ?? null)

  if (!options?.action) {
    $spriteActionQueue.set([])
  }

  $spriteState.set(name)
}

/** 播放动作序列：首个动作即刻进入 $spriteAction（3D 消费），后续进队列由 2D driver 推进。 */
export function playSpriteActionSequence(actions: readonly string[]): void {
  const [first, ...rest] = actions

  $spriteActionQueue.set(rest)
  $spriteAction.set(first ?? null)
}

// 程序化视线目标（精灵窗口归一 [-1,1]，与指针跟随同空间）：显式目标优先于指针
// （ritual walk 飞行途中锁定目标窗口中心）；null = 回到指针跟随。
export const $gazeTarget = atom<{ nx: number; ny: number } | null>(null)

export function setGazeTarget(target: { nx: number; ny: number }): void {
  $gazeTarget.set(target)
}

export function clearGazeTarget(): void {
  $gazeTarget.set(null)
}

let activityCounter = 0
let activityResetTimer: ReturnType<typeof setTimeout> | null = null

export function reportUserActivity(): void {
  const current = $spriteState.get()

  if (current !== 'idle' && current !== 'working') {
    return
  }

  activityCounter += 1

  if (activityCounter >= 6 && current === 'idle') {
    setSpriteState('working')
  }

  if (activityResetTimer) {
    clearTimeout(activityResetTimer)
  }

  activityResetTimer = setTimeout(() => {
    activityCounter = 0

    if ($spriteState.get() === 'working') {
      // ``working``（优先级 70）盖住 ``idle``（优先级 10）——不带 ``force: true`` 时
      // 计时器到期，但状态仍会卡在 working。显式强制退出，
      // 这样在用户停止活动达到配置窗口后精灵能回到 idle。
      setSpriteState('idle', { force: true })
    }
  }, 10000)
}

export function setDisturbanceTier(tier: DisturbanceTier): void {
  $userPreferredTier.set(tier)
  persistString('da.companion.disturbanceTier', tier)
}
