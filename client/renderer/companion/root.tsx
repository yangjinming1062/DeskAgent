import { useStore } from '@nanostores/react'
import { useEffect, useRef, useState } from 'react'

import { hydrateModel, hydrateWardrobe } from '@/companion/3d/model-store'
import { startActivityMonitor } from '@/companion/activity'
import { BootFailureOverlay } from '@/companion/boot/boot-failure-overlay'
import { useGatewayBoot } from '@/companion/boot/use-gateway-boot'
import { useGatewayRequest } from '@/companion/boot/use-gateway-request'
import { $chatOpen, setChatOpen } from '@/companion/chat-store'
import {
  $companionLifecycle,
  $voiceCallOpen,
  checkBedtimeAndAutoSleep,
  reportUserActivity,
  setCompanionLifecycle,
  wakeUpFromSleep
} from '@/companion/companion-store'
import { hydratePersona } from '@/companion/persona-store'
import { hydratePortrait } from '@/companion/portrait-store'
import { initSpatial } from '@/companion/spatial'
import { log } from '@/shared/lib/log'
import { $auth, applyAuthBroadcast, hydrateAuth, logout } from '@/shared/store/auth'
import { $gatewayState } from '@/shared/store/gateway'
import { notify } from '@/shared/store/notifications'
import { hydrateRunnerStatus } from '@/shared/store/runner-status'
import { strings } from '@/shared/strings'

import { Companion3D } from './3d/companion-3d'
import { ActivationOverlay } from './activation/activation-overlay'
import { ChatDock } from './chat-dock'
import { DeveloperOverlay } from './developer-overlay'
import { handleCompanionEvent } from './events'
import { handlePokeInteraction } from './interaction'
import { MemoryBrowser } from './memory-browser'
import { OnboardingFlow } from './onboarding/onboarding-flow'
import { speakProactive } from './proactive/proactive'
import { ProactiveBubble } from './proactive/proactive-bubble'
import { CompanionSettings } from './settings-overlay'
import { SpriteContextMenu } from './sprite/context-menu'
import { SpriteStage } from './sprite/sprite-stage'
import { VoiceCallDock } from './voice-call-dock'
import { checkCompanionVoiceValidity } from './voice-validity'

// Boots the gateway as a mount effect — so it only runs while authenticated.
// When $auth flips back to unauthenticated (logout/expiry) this unmounts and
// useGatewayBoot's cleanup tears the WS down. handleGatewayEvent dispatches the
// streaming chat frames onto the chat store + state machine (events.ts).
function GatewayBooter(): null {
  useGatewayBoot({
    handleGatewayEvent: handleCompanionEvent,
    onConnectionReady: () => {},
    onGatewayReady: () => {}
  })

  return null
}

export function CompanionRoot(): React.JSX.Element {
  const auth = useStore($auth)
  const gatewayState = useStore($gatewayState)
  const lifecycle = useStore($companionLifecycle)
  const chatOpen = useStore($chatOpen)
  const [onboardingOpen, setOnboardingOpen] = useState(false)
  const [activationOpen, setActivationOpen] = useState(false)
  const [voiceCallOpen, setVoiceCallOpen] = useState(false)
  useEffect(() => {
    $voiceCallOpen.set(voiceCallOpen)
  }, [voiceCallOpen])
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [memoryOpen, setMemoryOpen] = useState(false)
  const [contextMenuPos, setContextMenuPos] = useState<{ x: number; y: number } | null>(null)
  const { requestGateway } = useGatewayRequest()
  const validityCheckedRef = useRef(false)
  // Marks a sprite click that triggered the login flow — distinguishes a fresh
  // login from a cached-session boot, where the user must tap to open the wizard.
  const pendingOnboardingAutoOpenRef = useRef(false)

  useEffect(() => {
    void hydrateAuth()
  }, [])

  useEffect(() => initSpatial(), [])

  // Hydrate the runner-status atom once on mount — mirrors the hydrateAuth
  // pattern so companion-side consumers (activity.ts, etc.) can read
  // $runnerPhase without re-implementing the subscribe + sync-getter dance.
  useEffect(() => {
    void hydrateRunnerStatus()
  }, [])

  useEffect(() => {
    const off = window.deskagent.onAuthChanged(payload => applyAuthBroadcast(payload))

    return () => off()
  }, [])

  useEffect(() => {
    const off = window.deskagent.onSessionExpired(() => void logout())

    return () => off()
  }, [])

  // Tray menu "Log out" entry fires this bridge; the main-side logout also
  // triggers `onSessionExpired` on the next session check, but routing this
  // explicitly keeps the UI snappy when the user clicks the tray item.
  useEffect(() => {
    const off = window.deskagent.onTrayLogout?.(() => void logout())

    return () => off?.()
  }, [])

  // Clear on logout so the next login starts clean.
  useEffect(() => {
    if (auth.kind === 'unauthenticated') {
      pendingOnboardingAutoOpenRef.current = false
    }
  }, [auth.kind])

  // Dev-only: inject a test proactive message (Ctrl+Shift+P) to exercise the
  // companion.message receiver + bubble + TTS without the Backend send_message
  // path. Stripped in production builds.
  useEffect(() => {
    if (import.meta.env.PROD) {
      return
    }

    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey && e.shiftKey && (e.key === 'P' || e.key === 'p')) {
        e.preventDefault()
        void speakProactive('（测试）嘿，休息一下眼睛吧～')
      }
    }

    window.addEventListener('keydown', onKey)

    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // Route by onboarding.get_state.complete (not REST is_complete) so a mid-flow refresh after enterHatching doesn't kick the user out via a premature lifecycle='ready' + hydrateModel 404.
  useEffect(() => {
    if (auth.kind !== 'authenticated') {
      setCompanionLifecycle('unauthed')
      setOnboardingOpen(false)

      return
    }

    let cancelled = false
    setCompanionLifecycle('unauthed')
    Promise.all([
      window.deskagent.api<{ is_complete?: boolean }>({ path: '/api/companion/persona' }),
      requestGateway<{ complete?: boolean }>('onboarding.get_state', {})
    ])
      .then(([persona, state]) => {
        if (cancelled) {
          return
        }

        const onboardingDone = state?.complete ?? persona?.is_complete ?? false
        setCompanionLifecycle(onboardingDone ? 'ready' : 'onboarding')

        // Only pull the portrait once we know one exists — during onboarding
        // the GET would 404, which the renderer would silently catch but the
        // main process still logs to stderr.
        if (onboardingDone) {
          void hydratePortrait()
        }
      })
      .catch(() => {
        if (!cancelled) {
          setCompanionLifecycle('onboarding')
        }
      })

    return () => {
      cancelled = true
    }
  }, [auth.kind, requestGateway])

  // Auto-open the wizard once after a fresh login resolves to 'onboarding'.
  useEffect(() => {
    if (
      auth.kind === 'authenticated' &&
      lifecycle === 'onboarding' &&
      !onboardingOpen &&
      pendingOnboardingAutoOpenRef.current
    ) {
      pendingOnboardingAutoOpenRef.current = false
      setOnboardingOpen(true)
    }
  }, [auth.kind, lifecycle, onboardingOpen])

  const authed = auth.kind === 'authenticated'
  const showOnboarding = authed && lifecycle === 'onboarding' && onboardingOpen
  const showReady = authed && lifecycle === 'ready'

  useEffect(() => {
    if (lifecycle !== 'ready') {
      return
    }

    checkBedtimeAndAutoSleep()

    const timer = setInterval(() => {
      checkBedtimeAndAutoSleep()
    }, 60000)

    const onKey = () => reportUserActivity()
    window.addEventListener('keydown', onKey)

    const stopActivity = startActivityMonitor()
    void Promise.all([hydratePersona(), hydrateModel(), hydrateWardrobe()])

    return () => {
      clearInterval(timer)
      window.removeEventListener('keydown', onKey)
      stopActivity()
    }
  }, [lifecycle])

  // Detect a cached companion voice id that the cloud catalog no longer lists
  // (provider pruned/renamed voices, or provider switch). Backend tolerates
  // unknown ids, so this is a one-time prompt — not a hard error.
  useEffect(() => {
    if (lifecycle !== 'ready' || gatewayState !== 'open' || validityCheckedRef.current) {
      return
    }

    validityCheckedRef.current = true

    void checkCompanionVoiceValidity(requestGateway).then(result => {
      if (result.valid) {
        return
      }

      notify({
        kind: 'warning',
        title: strings.notifications.voice.invalidTitle,
        message: strings.notifications.voice.invalidMessage(result.name),
        action: { label: strings.notifications.voice.invalidAction, onClick: () => setSettingsOpen(true) }
      })
    })
  }, [lifecycle, gatewayState, requestGateway])

  const onTap = () => {
    if (showReady) {
      wakeUpFromSleep()
      handlePokeInteraction()

      return
    }

    // Pre-auth: click opens the activation overlay in the companion window.
    if (!authed) {
      pendingOnboardingAutoOpenRef.current = true
      setActivationOpen(true)

      return
    }

    // Authenticated but onboarding incomplete: click opens the onboarding flow.
    if (lifecycle === 'onboarding') {
      setOnboardingOpen(true)
    }
  }

  // Plan §4.3: double-tap the ready companion to open Chat. Single-tap poke
  // reactions (LLM-generated) arrive in a later slice.
  const onDoubleTap = () => {
    if (showReady) {
      setChatOpen(true)
    }
  }

  // Onboarding completion fires the 3D model generation (base_texture
  // provider is instant — model.ready arrives in the same tick and the
  // engine reloads). Failure is silent: the user can retry from settings.
  const onOnboardingComplete = () => {
    setOnboardingOpen(false)
    setCompanionLifecycle('ready')
    void window.deskagent
      .api<{ id?: number; status?: string }>({ path: '/api/companion/model', method: 'POST', body: {} })
      .catch(err => log.warn('companion', 'initial model generation failed:', err))
  }

  return (
    <>
      {activationOpen && !authed && <ActivationOverlay onClose={() => setActivationOpen(false)} />}
      {showOnboarding && <OnboardingFlow onCompleted={onOnboardingComplete} />}
      <SpriteStage
        onContextMenu={e => {
          if (showReady) {
            setContextMenuPos({ x: e.clientX, y: e.clientY })
          }
        }}
        onDoubleTap={onDoubleTap}
        onTap={onTap}
      >
        {showOnboarding ? null : <Companion3D />}
      </SpriteStage>
      {showReady && contextMenuPos && (
        <SpriteContextMenu
          onClose={() => setContextMenuPos(null)}
          onOpenMemory={() => {
            setChatOpen(false)
            setVoiceCallOpen(false)
            setMemoryOpen(true)
          }}
          onOpenSettings={() => {
            setChatOpen(false)
            setVoiceCallOpen(false)
            setSettingsOpen(true)
          }}
          onOpenVoiceCall={() => {
            setChatOpen(false)
            setVoiceCallOpen(true)
          }}
          x={contextMenuPos.x}
          y={contextMenuPos.y}
        />
      )}
      {showReady && chatOpen && (
        <ChatDock
          onClose={() => setChatOpen(false)}
          onOpenVoiceCall={() => {
            setChatOpen(false)
            setVoiceCallOpen(true)
          }}
        />
      )}
      {showReady && voiceCallOpen && <VoiceCallDock onClose={() => setVoiceCallOpen(false)} />}
      {showReady && settingsOpen && <CompanionSettings onClose={() => setSettingsOpen(false)} />}
      {showReady && memoryOpen && <MemoryBrowser onClose={() => setMemoryOpen(false)} />}
      {showReady && <ProactiveBubble />}
      <BootFailureOverlay />
      <DeveloperOverlay />
      {authed && <GatewayBooter />}
    </>
  )
}
