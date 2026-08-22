import { atom } from 'nanostores'

import { $effectiveTierOverride, $userPreferredTier, type DisturbanceTier } from '@/companion/companion-store'
import { $gateway } from '@/shared/store/gateway'
import { $runnerPhase } from '@/shared/store/runner-status'

import { $llmAffect } from './prefs'

// 本地环境信号取自 Runner 的 system.* 工具（plan §8），
// 绕过 LLM——伙伴层直接基于这些信号做推理。Runner 离线时 poll 是空操作，
// atom 保持默认值。

export const $screenLocked = atom<boolean>(false)
// 最近一次有限空闲秒数；-1 表示无信号（Runner 离线或探测失败），调用方应按未知处理。
export const $lastIdleSeconds = atom<number>(-1)

export type FocusCategory = 'ide' | 'music' | 'reader' | 'gaming' | 'browsing' | 'other' | 'unknown'

export interface FocusContext {
  category: FocusCategory
  fullscreen: boolean
  windowGeom?: { x: number; y: number; w: number; h: number }
}

export const $focusContext = atom<FocusContext | null>(null)

const POLL_INTERVAL_MS = 30_000

// 空闲触发的上下文 affect（ARCHITECTURE.md §7.6）。当用户闲置时间超过
// IDLE_THRESHOLD_SECONDS 且冷却窗口已过，向后端的 `companion.check_affect`
// RPC 发起探测，让 LLM 结合人格（长期记忆）推理伙伴是否应表达一个
// 上下文相关的情绪。客户端掌握触发时机；后端掌握情绪推理。
const IDLE_THRESHOLD_SECONDS = 30 * 60
const CHECK_COOLDOWN_MS = 60 * 60 * 1000

// 打扰档位推送去重：只有当生效档位值变化时才往后端推；轮询节拍
// (POLL_INTERVAL_MS, 30 秒) 远大于任何合理的节流阈值，
// 所以单凭值去重就够了。
let timer: ReturnType<typeof setInterval> | null = null
let lastAffectCheckAt = 0
let lastTierPushed: { value: DisturbanceTier; at: number } | null = null

// Runner 网关状态。轮询只在 bridge 达到 `running` 后才发起 `runnerInvoke`；
// 否则每个周期 `pollOnce` 都会在主进程里记四次 "Runner is not connected"
// 拒绝，毫无意义。bridge 生命周期写在共享的 `$runnerPhase` atom 里
// （见 `@/shared/store/runner-status`）；本模块订阅其状态变化，
// 并把布尔值本地缓存供 setInterval tick 网关使用。
let runnerReady = false
let offPhaseSub: (() => void) | null = null

function maybeTriggerAffectCheck(idleSeconds: number, locked: boolean): void {
  if (!$llmAffect.get() || locked || idleSeconds < IDLE_THRESHOLD_SECONDS) {
    return
  }

  const now = Date.now()

  if (now - lastAffectCheckAt < CHECK_COOLDOWN_MS) {
    return
  }

  const hour = new Date().getHours()

  // 深夜时段跳过本轮 affect 轮询。
  if (hour >= 23 || hour < 7) {
    return
  }

  lastAffectCheckAt = now
  const gateway = $gateway.get()
  void gateway
    ?.request('companion.check_affect', {
      idle_seconds: idleSeconds,
      local_hour: hour
    })
    .catch(() => {
      /* 后端离线或 RPC 失败——静默，下次轮询冷却后重试 */
    })
}

interface FocusedAppInfo {
  name?: string
  title?: string
  bundle?: string
  x?: number
  y?: number
  w?: number
  h?: number
}

type CategoryTable = Record<Exclude<FocusCategory, 'unknown' | 'other'>, readonly string[]>

const WINDOWS_ALLOWLIST = {
  ide: [
    'code.exe',
    'devenv.exe',
    'idea64.exe',
    'pycharm64.exe',
    'webstorm64.exe',
    'sublime_text.exe',
    'nvim.exe',
    'vim.exe',
    'clion.exe',
    'rider.exe',
    'rubymine64.exe',
    'goland64.exe',
    'atom.exe'
  ],
  music: ['spotify.exe', 'qqmusic.exe', 'cloudmusic.exe', 'musicbee.exe', 'foobar2000.exe'],
  reader: [
    'acrobat.exe',
    'acrord32.exe',
    'sumatrapdf.exe',
    'zathura.exe',
    'calibre.exe',
    'ebookreader.exe',
    'foxitreader.exe'
  ],
  gaming: [
    'steam.exe',
    'epicgameslauncher.exe',
    'minecraft.exe',
    'riotclientux.exe',
    'riotclientservices.exe',
    'battle.net.exe',
    'origin.exe',
    'steamwebhelper.exe'
  ],
  browsing: ['chrome.exe', 'firefox.exe', 'msedge.exe', 'brave.exe', 'opera.exe', 'vivaldi.exe']
} as const satisfies CategoryTable

const MACOS_BUNDLE_PREFIXES = {
  ide: [
    'com.microsoft.vscode',
    'com.jetbrains.',
    'com.sublimetext.',
    'com.qvacua.vim',
    'org.vim.macvim',
    'com.github.atom'
  ],
  music: ['com.spotify.client', 'com.netease.163music', 'com.apple.music'],
  reader: ['com.adobe.acrobat', 'com.adobe.reader', 'com.apple.ibooks', 'read.amazon.kindle'],
  gaming: ['com.valvesoftware.steam', 'com.epicgames.epicgameslauncher'],
  browsing: [
    'com.google.chrome',
    'org.mozilla.firefox',
    'com.microsoft.edgemac',
    'com.brave.browser',
    'com.operasoftware.opera'
  ]
} as const satisfies CategoryTable

function classifyWindows(info: FocusedAppInfo): FocusCategory {
  const name = (info.name ?? '').toLowerCase()

  for (const cat of ['ide', 'music', 'reader', 'gaming', 'browsing'] as const) {
    for (const token of WINDOWS_ALLOWLIST[cat]) {
      if (name === token || name.endsWith(`\\${token}`)) {
        return cat
      }
    }
  }

  return 'unknown'
}

function classifyMacos(info: FocusedAppInfo): FocusCategory {
  const bundle = (info.bundle ?? '').toLowerCase()
  const name = (info.name ?? '').toLowerCase()

  for (const cat of ['ide', 'music', 'reader', 'gaming', 'browsing'] as const) {
    for (const prefix of MACOS_BUNDLE_PREFIXES[cat]) {
      if (bundle.startsWith(prefix) || name.includes(prefix.replace('com.', ''))) {
        return cat
      }
    }
  }

  return 'unknown'
}

export function classifyFocusedApp(info: FocusedAppInfo): FocusCategory {
  if (!info || Object.keys(info).length === 0) {
    return 'unknown'
  }

  const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform)

  return isMac ? classifyMacos(info) : classifyWindows(info)
}

const IMMERSIVE_CATEGORIES: ReadonlySet<FocusCategory> = new Set(['ide', 'gaming', 'reader'])

function computeLocalEffectiveTier(userPreferred: DisturbanceTier, ctx: FocusContext | null): DisturbanceTier {
  // 手动 ``quiet`` 锁定：任何情况下都不被覆盖。
  if (userPreferred === 'quiet') {
    return 'quiet'
  }

  if (!ctx) {
    return userPreferred
  }

  if (ctx.fullscreen || IMMERSIVE_CATEGORIES.has(ctx.category)) {
    return 'quiet'
  }

  return userPreferred
}

function maybePushTierOverride(): void {
  const preferred = $userPreferredTier.get()
  const ctx = $focusContext.get()
  const desired = computeLocalEffectiveTier(preferred, ctx)

  // 镜像后端 sidecar 的行为：写入 override atom 让 $effectiveTier 重算，
  // 并把派生出的生效档位推给后端。值未变则跳过 set——订阅者会级联到所有订阅者。
  const nextOverride = desired === preferred ? null : desired

  if ($effectiveTierOverride.get() !== nextOverride) {
    $effectiveTierOverride.set(nextOverride)
  }

  // 仅按值去重：只有生效值变化时才推送。
  if (lastTierPushed && lastTierPushed.value === desired) {
    return
  }

  lastTierPushed = { value: desired, at: Date.now() }

  const gateway = $gateway.get()
  void gateway?.request('companion.set_disturbance_tier', { tier: desired }).catch(() => {
    /* 后端离线——下次轮询周期会重试推送 */
  })
}

// Runner 的活动快照聚合。单个 ``system.snapshot`` 往返替代四次
// 独立的 ``system.*`` 探测——数据结构相同，
// 但只需一次 IPC + WS 消息，而非四次。当底层 OS API 缺失时，
// 返回与各独立工具相同的探针安全默认值（idle=-1.0, locked=false,
// focused={}, fullscreen=false）。
interface SystemSnapshot {
  idle_seconds?: number
  locked?: boolean
  focused_app?: FocusedAppInfo | Record<string, never>
  fullscreen?: boolean
}

async function pollOnce(): Promise<void> {
  const desktop = window.spiritagent

  if (!desktop?.runnerInvoke) {
    return
  }

  // ``system.snapshot`` 聚合全部四个信号。任一拒绝会保留所有 atom
  // 不动（探针失败时黏住上次值），与原先四次调用 ``Promise.all`` 的
  // 逐探针语义保持一致。
  const snapshotResult = await desktop.runnerInvoke('system.snapshot', {}).catch(() => null)

  if (snapshotResult === null) {
    // 探测失败——atom 保留上次已知值。基于当前 atom 状态重新计算
    // 档位 override，避免探测中断后陈旧的 $focusContext 把档位
    // 永久钉住。
    maybePushTierOverride()

    return
  }

  const snapshot = snapshotResult as SystemSnapshot

  // 锁屏场景：仅在快照包含该字段时才更新原子。
  if (snapshot.locked !== undefined) {
    const isLocked = Boolean(snapshot.locked)

    if (isLocked !== $screenLocked.get()) {
      $screenLocked.set(isLocked)
    }
  }

  const idleSeconds = Number(snapshot.idle_seconds ?? 0)

  // ``Number('abc')`` 返回 NaN；而 ``NaN < N`` 永远为 false，
  // 没有显式守卫时下面的冷却网关会把 NaN 透传给后端的 LLM prompt。
  // 把任何非有限值当作缺失信号处理。
  if (!Number.isFinite(idleSeconds)) {
    return
  }

  // 缓存最近一次有限空闲值，其他模块按需读取，不必再向 Runner 发起请求。
  // 这里的 -1 表示"本周期无信号"——调用方按未知处理并跳过该字段。
  $lastIdleSeconds.set(idleSeconds)

  const isLockedKnown = snapshot.locked !== undefined
  maybeTriggerAffectCheck(isLockedKnown ? idleSeconds : -1, $screenLocked.get())

  // 全屏状态与聚焦应用独立：即使聚焦应用分类失败，也单独跟踪全屏位，
  // 这样只要当前是全屏窗口，无论聚焦分类因何缺失，
  // 主动出击始终被压制。
  const fullscreenProbeOk = snapshot.fullscreen !== undefined

  const fullscreen = fullscreenProbeOk ? Boolean(snapshot.fullscreen) : ($focusContext.get()?.fullscreen ?? false)

  if (snapshot.focused_app && Object.keys(snapshot.focused_app).length > 0) {
    const focused = snapshot.focused_app as FocusedAppInfo
    const category = classifyFocusedApp(focused)

    const windowGeom =
      focused.w != null && focused.h != null
        ? { x: focused.x ?? 0, y: focused.y ?? 0, w: focused.w, h: focused.h }
        : undefined

    const cur = $focusContext.get()

    const geomChanged =
      (windowGeom?.x ?? -1) !== (cur?.windowGeom?.x ?? -1) || (windowGeom?.y ?? -1) !== (cur?.windowGeom?.y ?? -1)

    if (!cur || cur.category !== category || cur.fullscreen !== fullscreen || geomChanged) {
      $focusContext.set({ category, fullscreen, windowGeom })
    }
  } else if (fullscreenProbeOk) {
    // focused-app 探测为空但 fullscreen 成功：保留分类
    // （以及 override atom），但仍要更新 fullscreen 位，
    // 这样新检测到的全屏窗口无需依赖一次成功的 focused-app 探测。
    const cur = $focusContext.get()

    if (cur && cur.fullscreen !== fullscreen) {
      $focusContext.set({ category: cur.category, fullscreen, windowGeom: cur.windowGeom })
    }
  }

  maybePushTierOverride()
}

export function startActivityMonitor(): () => void {
  if (timer) {
    return stopActivityMonitor
  }

  let firstPollDone = false

  const kickFirstPoll = () => {
    if (firstPollDone) {
      return
    }

    firstPollDone = true
    void pollOnce()
  }

  // 订阅共享的 phase atom（见 @/shared/store/runner-status）。
  // nanostore 在订阅时会用当前值触发一次回调，
  // 所以如果 bridge 已经是 `running`（atom 已通过 runnerGetState 水合），
  // 首次轮询会被 kick。后续的 `running` 事件让 `runnerReady` 保持 true，
  // 但一次性 latch 避免在恢复时爆发轮询（刻意如此：不爆发，
  // 只是重新并入 30 秒节拍）。
  offPhaseSub = $runnerPhase.subscribe(phase => {
    if (phase === 'running') {
      runnerReady = true
      kickFirstPoll()
    } else if (phase === 'stopped' || phase === 'error') {
      // bridge 恢复后会再次发出 `running`；在此之前 setInterval tick
      // 是空操作，避免在 IPC 错误日志里不断刷 "Runner is not connected"。
      runnerReady = false
    }
  })

  timer = setInterval(() => {
    if (!runnerReady) {
      return
    }

    void pollOnce()
  }, POLL_INTERVAL_MS)

  return stopActivityMonitor
}

export function stopActivityMonitor(): void {
  if (timer) {
    clearInterval(timer)
    timer = null
  }

  if (offPhaseSub) {
    offPhaseSub()
    offPhaseSub = null
  }

  runnerReady = false
}

// 客户端 stats RPC 节流。低于阈值（任意 kind < 10）时客户端发送每次事件，
// 让后端的每日计数器及时累计；一旦某个 kind 越过阈值，行已经写入，
// 同 kind 的后续事件只需刷新行内容。每 kind 至多每 60 秒采样一次，
// 在不损失有意义的聚合粒度的前提下限制越过阈值后的 DB 写入频率。
// 后端的 ``record_interaction`` 仍会增加内存计数器，
// 因此客户端短暂丢事件不会让 ``threshold_met`` 退回 false
// （计数器只在 UTC 日切换时重置）。
const STATS_POST_THRESHROTTLE_MS = 60_000

const _localStatsCounters: Record<'poke' | 'chat_turn', number> = {
  poke: 0,
  chat_turn: 0
}

const _lastStatsSentAt: Record<'poke' | 'chat_turn', number> = {
  poke: 0,
  chat_turn: 0
}

export function reportInteractionStat(kind: 'poke' | 'chat_turn'): void {
  const gateway = $gateway.get()

  if (!gateway) {
    return
  }

  _localStatsCounters[kind] += 1

  // 阈值与后端的 ``STATS_THRESHOLD = 10`` 对齐。低于阈值时
  // 每次事件都发送，让每日计数器及时累加；越过阈值后，
  // 每 kind 每分钟最多合并成一次 RPC——每日行已落库，
  // 后端的内存计数器才是 ``threshold_met`` 的真值。后续事件
  // 在每次合并发送时仍会更新行内容（高峰小时 / hour_buckets）。
  const now = Date.now()

  if (_localStatsCounters[kind] > 10 && now - _lastStatsSentAt[kind] < STATS_POST_THRESHROTTLE_MS) {
    return
  }

  _lastStatsSentAt[kind] = now

  void gateway.request('companion.record_interaction_stats', { kind, hour: new Date().getHours() }).catch(() => {
    /* 即发即忘；失败静默吞掉 */
  })
}
