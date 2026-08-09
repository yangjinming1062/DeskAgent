import { atom } from 'nanostores'

import { $effectiveTierOverride, $userPreferredTier, type DisturbanceTier } from '@/companion/companion-store'
import { $gateway } from '@/shared/store/gateway'

// Local environment signals polled from the Runner's system.* tools (plan §8),
// bypassing the LLM — the companion reasons about them directly. Polls no-op
// while the Runner is offline and the atoms keep their defaults.

export const $screenLocked = atom<boolean>(false)
// Last finite idle-seconds reading from the activity poll. -1 means no signal
// yet (runner offline, probe failed, etc.) — callers should treat that as
// "unknown" and either skip the field or send 0.
export const $lastIdleSeconds = atom<number>(-1)

export type FocusCategory = 'ide' | 'music' | 'reader' | 'gaming' | 'browsing' | 'other' | 'unknown'

export interface FocusContext {
  category: FocusCategory
  fullscreen: boolean
  windowGeom?: { x: number; y: number; w: number; h: number }
}

export const $focusContext = atom<FocusContext | null>(null)

const POLL_INTERVAL_MS = 30_000

// Idle-triggered contextual affect (ARCHITECTURE.md §7.6). When the user has
// been inactive past IDLE_THRESHOLD_SECONDS and the cooldown window has
// elapsed, ping the backend's `companion.check_affect` RPC so the LLM can
// reason (persona + memory) whether the companion should express a contextual
// emotion. The desktop owns trigger timing; the backend owns emotion reasoning.
const IDLE_THRESHOLD_SECONDS = 30 * 60
const CHECK_COOLDOWN_MS = 60 * 60 * 1000

// Tier-push dedup: only push to the backend when the effective tier value
// changes; the polling cadence (POLL_INTERVAL_MS, 30s) is much larger than
// any reasonable throttle, so dedup on value alone is sufficient.
let timer: ReturnType<typeof setInterval> | null = null
let lastAffectCheckAt = 0
let lastTierPushed: { value: DisturbanceTier; at: number } | null = null

// Runner-gate state. The poll only fires `runnerInvoke` once the bridge has
// reached `running`; until then `pollOnce` would log four "Runner is not
// connected" rejections per cycle in the main process for no value. We use
// the subscribe + sync-getter pattern (mirrors hydrateAuth → auth:get-session
// + onAuthChanged): subscribe to `onRunnerStatus` for future transitions,
// AND query `runnerGetState` once on mount in case the bridge was already
// `running` before we subscribed (Electron's IPC has no event replay).
let runnerReady = false
let offRunnerStatus: (() => void) | null = null

function maybeTriggerAffectCheck(idleSeconds: number, locked: boolean): void {
  if (locked || idleSeconds < IDLE_THRESHOLD_SECONDS) {
    return
  }

  const now = Date.now()

  if (now - lastAffectCheckAt < CHECK_COOLDOWN_MS) {
    return
  }

  const hour = new Date().getHours()

  // Quiet hours (23-7, synced with companion-store.checkBedtimeAndAutoSleep):
  // skip so an affect cue doesn't wake the companion past SLEEPING.
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
      /* backend offline or RPC failed — silent, next poll will retry after cooldown */
    })
}

// ---------------------------------------------------------------------------
// Focused-app classification
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// Tier-override derivation + push
// ---------------------------------------------------------------------------

const IMMERSIVE_CATEGORIES: ReadonlySet<FocusCategory> = new Set(['ide', 'gaming', 'reader'])

function computeLocalEffectiveTier(userPreferred: DisturbanceTier, ctx: FocusContext | null): DisturbanceTier {
  // Manual ``quiet`` lock-in: never overridden.
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

  // Mirror the backend's sidecar: write the override atom so $effectiveTier
  // recomputes, and push the derived effective tier to the backend. Skip the
  // set when unchanged — atom updates cascade to all subscribers.
  const nextOverride = desired === preferred ? null : desired

  if ($effectiveTierOverride.get() !== nextOverride) {
    $effectiveTierOverride.set(nextOverride)
  }

  // Dedup on value alone: only push when the effective value changes.
  if (lastTierPushed && lastTierPushed.value === desired) {
    return
  }

  lastTierPushed = { value: desired, at: Date.now() }

  const gateway = $gateway.get()
  void gateway?.request('companion.set_disturbance_tier', { tier: desired }).catch(() => {
    /* backend offline — push will retry on next poll cycle */
  })
}

// ---------------------------------------------------------------------------
// Poll loop
// ---------------------------------------------------------------------------

interface IsFullscreenResult {
  fullscreen?: boolean
}

async function pollOnce(): Promise<void> {
  const desktop = window.deskagent

  if (!desktop?.runnerInvoke) {
    return
  }

  // Per-probe try/catch preserves the original "sticky on probe failure"
  // semantics: any rejection leaves the corresponding atom untouched instead
  // of being coerced to a safe-default false. ``null`` from the catch is
  // treated as "no fresh signal this cycle".
  const [lockedResult, idleResult, focusedResult, fullscreenResult] = await Promise.all([
    desktop.runnerInvoke('system.is_screen_locked', {}).catch(() => null),
    desktop.runnerInvoke('system.get_idle_seconds', {}).catch(() => null),
    desktop.runnerInvoke('system.get_focused_app', {}).catch(() => null),
    desktop.runnerInvoke('system.is_fullscreen', {}).catch(() => null)
  ])

  // Screen-lock: only update the atom when the probe succeeded. A failed
  // probe (runner offline / IPC dropped / laptop resuming) keeps the last
  // known value rather than silently un-suppressing proactive messages on
  // a locked workstation.
  if (lockedResult !== null) {
    const isLocked = Boolean((lockedResult as { locked?: boolean }).locked)

    if (isLocked !== $screenLocked.get()) {
      $screenLocked.set(isLocked)
    }
  }

  const idleSeconds = idleResult !== null ? Number((idleResult as { idle_seconds?: number }).idle_seconds ?? 0) : -1

  // ``Number('abc')`` returns NaN; ``NaN < N`` is always false, so without an
  // explicit guard the cooldown gate below would pass NaN through to the
  // backend LLM prompt. Treat any non-finite value as a missing signal.
  if (!Number.isFinite(idleSeconds)) {
    return
  }

  // Cache the latest finite idle reading so other modules can read it on
  // demand without re-querying the runner. -1 here means "no signal this
  // cycle" — callers treat that as unknown and skip the field.
  $lastIdleSeconds.set(idleSeconds)

  const isLockedKnown = lockedResult !== null
  maybeTriggerAffectCheck(isLockedKnown ? idleSeconds : -1, $screenLocked.get())

  // Fullscreen is independent of focused-app: we still track it on its own
  // even if focused-app classification failed, so an active fullscreen
  // window always suppresses proactive outreach regardless of why focus
  // classification is missing.
  const fullscreenProbeOk = fullscreenResult !== null

  const fullscreen = fullscreenProbeOk
    ? Boolean((fullscreenResult as IsFullscreenResult).fullscreen)
    : ($focusContext.get()?.fullscreen ?? false)

  if (focusedResult !== null) {
    const focused = (focusedResult as { focused_app?: FocusedAppInfo }).focused_app ?? null

    if (focused !== null) {
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
    }
  } else if (fullscreenProbeOk) {
    // focused-app probe failed but fullscreen succeeded: keep the
    // category (and the override atom if any) but still update the
    // fullscreen bit so a freshly-detected fullscreen window doesn't
    // require a successful focused-app probe.
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

  const desktop = window.deskagent

  let firstPollDone = false

  const kickFirstPoll = () => {
    if (firstPollDone) {return}
    firstPollDone = true
    void pollOnce()
  }

  // Sync + subscribe: query the bridge's current phase so a bridge that
  // already reached `running` before we mounted (no event replay) still
  // gets polled. If it's not up yet, the `onRunnerStatus` subscription below
  // catches the eventual `running` event — no setTimeout, no race.
  void desktop?.runnerGetState?.()
    .then(state => {
      if (state?.phase === 'running') {
        runnerReady = true
        kickFirstPoll()
      }
    })
    .catch(() => {
      // Bridge probe failed (older preload contract or IPC transport error).
      // The subscription below is the fallback path; polls stay gated on
      // `runnerReady` so we don't issue `runnerInvoke` against a bridge we
      // couldn't introspect.
    })

  const subscribed = desktop?.onRunnerStatus?.(ev => {
    if (ev.type === 'running') {
      runnerReady = true
      kickFirstPoll()
    } else if (ev.type === 'stopped' || ev.type === 'error') {
      // Bridge will re-emit `running` once it recovers; until then the
      // setInterval tick is a no-op so we don't spam "Runner is not
      // connected" through the IPC error log path.
      runnerReady = false
    }
  })

  offRunnerStatus = subscribed ?? null

  timer = setInterval(() => {
    if (!runnerReady) {return}
    void pollOnce()
  }, POLL_INTERVAL_MS)

  return stopActivityMonitor
}

export function stopActivityMonitor(): void {
  if (timer) {
    clearInterval(timer)
    timer = null
  }

  if (offRunnerStatus) {
    offRunnerStatus()
    offRunnerStatus = null
  }

  runnerReady = false
}

// Client-side throttle for stats RPCs. Pre-threshold (any kind < 10)
// the desktop fires every event so the backend's daily counter ticks
// up promptly; once a kind crosses the threshold the row is already
// written, so subsequent events of the same kind only need to refresh
// the row content. Sampling at most every 60s per kind keeps the
// post-threshold DB-write rate bounded without losing meaningful
// aggregation. The backend's ``record_interaction`` still increments
// the in-memory counter, so a brief client-side drop never causes a
// regression to ``threshold_met=false`` (the counter only resets on
// UTC day rollover).
const STATS_POST_THRESHROTTLE_MS = 60_000

const _localStatsCounters: Record<'poke' | 'drag' | 'chat_turn', number> = {
  poke: 0,
  drag: 0,
  chat_turn: 0
}

const _lastStatsSentAt: Record<'poke' | 'drag' | 'chat_turn', number> = {
  poke: 0,
  drag: 0,
  chat_turn: 0
}

export function reportInteractionStat(kind: 'poke' | 'drag' | 'chat_turn'): void {
  const gateway = $gateway.get()

  if (!gateway) {
    return
  }

  _localStatsCounters[kind] += 1

  // Threshold matches the backend's ``STATS_THRESHOLD = 10``. Below it,
  // fire every event so the daily counter ticks promptly; above it,
  // coalesce to one RPC per minute per kind — the daily row is already
  // persisted, and the in-memory counter on the backend is the source
  // of truth for ``threshold_met``. Subsequent events still update the
  // row content (peak hour / hour_buckets) on each coalesced send.
  const now = Date.now()

  if (_localStatsCounters[kind] > 10 && now - _lastStatsSentAt[kind] < STATS_POST_THRESHROTTLE_MS) {
    return
  }

  _lastStatsSentAt[kind] = now

  void gateway.request('companion.record_interaction_stats', { kind, hour: new Date().getHours() }).catch(() => {
    /* fire-and-forget; failure silently swallowed */
  })
}
