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
import { hydratePortrait, hydratePortraitHistory } from '@/companion/portrait-store'
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
import { $contextMenuPos } from './sprite/context-menu-store'
import { SpriteStage } from './sprite/sprite-stage'
import { StaticSprite } from './static-sprite/StaticSprite'
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
  const { requestGateway } = useGatewayRequest()

  // Sprite-window docks are mutually exclusive — opening one closes the
  // others so popups never stack on screen.
  const openDock = (kind: 'chat' | 'memory' | 'settings' | 'voice'): void => {
    setChatOpen(kind === 'chat')
    setVoiceCallOpen(kind === 'voice')
    setSettingsOpen(kind === 'settings')
    setMemoryOpen(kind === 'memory')
  }

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
    const off = window.spiritagent.onAuthChanged(payload => applyAuthBroadcast(payload))

    return () => off()
  }, [])

  useEffect(() => {
    const off = window.spiritagent.onSessionExpired(() => void logout())

    return () => off()
  }, [])

  // Tray menu "Log out" entry fires this bridge; the main-side logout also
  // triggers `onSessionExpired` on the next session check, but routing this
  // explicitly keeps the UI snappy when the user clicks the tray item.
  useEffect(() => {
    const off = window.spiritagent.onTrayLogout?.(() => void logout())

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

  // Query onboarding state on auth.
  // 1) Use GET /api/companion/onboarding/state (REST) for instant authoritative check.
  // 2) Fallback to requestGateway('onboarding.get_state') if available.
  // 3) Only set lifecycle='ready' when state?.complete === true.
  //    Never fall back to persona.is_complete (which is true mid-onboarding after character questions are saved).
  // 4) If state?.complete is not true, set lifecycle='onboarding' and setOnboardingOpen(true).
  useEffect(() => {
    if (auth.kind !== 'authenticated') {
      setCompanionLifecycle('unauthed')
      setOnboardingOpen(false)

      return
    }

    let cancelled = false

    const checkState = async () => {
      let state: { complete?: boolean } | null = null

      try {
        state = await window.spiritagent.api<{ complete?: boolean }>({
          path: '/api/companion/onboarding/state'
        })
      } catch {
        state = await requestGateway<{ complete?: boolean }>('onboarding.get_state', {}).catch(() => null)
      }

      if (cancelled) {
        return
      }

      const onboardingDone = state?.complete === true
      setCompanionLifecycle(onboardingDone ? 'ready' : 'onboarding')
      setOnboardingOpen(!onboardingDone)

      if (onboardingDone) {
        void hydratePortrait()
        void hydratePortraitHistory()
      }
    }

    void checkState()

    return () => {
      cancelled = true
    }
  }, [auth.kind, requestGateway])

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
        action: { label: strings.notifications.voice.invalidAction, onClick: () => openDock('settings') }
      })
    })
  }, [lifecycle, gatewayState, requestGateway])

  const onTap = () => {
    if (authed) {
      if (lifecycle === 'onboarding') {
        setOnboardingOpen(true)

        return
      }

      wakeUpFromSleep()
      handlePokeInteraction()

      return
    }

    // Pre-auth: click opens the activation overlay in the companion window.
    pendingOnboardingAutoOpenRef.current = true
    setActivationOpen(true)
  }

  // Plan §4.3: double-tap the ready companion to open Chat.
  const onDoubleTap = () => {
    if (authed) {
      if (lifecycle === 'onboarding') {
        setOnboardingOpen(true)

        return
      }

      openDock('chat')

      return
    }

    setActivationOpen(true)
  }

  // Onboarding completion fires the 3D model generation (base_texture
  // provider is instant — model.ready arrives in the same tick and the
  // engine reloads). POST /model is server-side idempotent when the body
  // model already exists, so a resume-fire here is safe — no client guard
  // needed. Failure is silent: the user can retry from settings.
  const onOnboardingComplete = () => {
    setOnboardingOpen(false)
    setCompanionLifecycle('ready')

    void window.spiritagent
      .api<{ id?: number; status?: string }>({ path: '/api/companion/model', method: 'POST', body: {} })
      .catch(err => log.warn('companion', 'initial model generation failed:', err))
  }

  return (
    <>
      {activationOpen && !authed && <ActivationOverlay onClose={() => setActivationOpen(false)} />}
      {showOnboarding && <OnboardingFlow onCompleted={onOnboardingComplete} />}
      <SpriteStage
        onContextMenu={e => {
          $contextMenuPos.set({ x: e.clientX, y: e.clientY })
        }}
        onDoubleTap={onDoubleTap}
        onTap={onTap}
      >
        {showOnboarding ? null : (
          <>
            <Companion3D />
            <StaticSprite />
          </>
        )}
      </SpriteStage>
      <SpriteContextMenu
        onOpenActivation={() => setActivationOpen(true)}
        onOpenChat={() => openDock('chat')}
        onOpenMemory={() => openDock('memory')}
        onOpenSettings={() => openDock('settings')}
        onOpenVoiceCall={() => openDock('voice')}
      />
      {authed && chatOpen && <ChatDock onClose={() => setChatOpen(false)} onOpenVoiceCall={() => openDock('voice')} />}
      {authed && voiceCallOpen && <VoiceCallDock onClose={() => setVoiceCallOpen(false)} />}
      {authed && settingsOpen && <CompanionSettings onClose={() => setSettingsOpen(false)} />}
      {authed && memoryOpen && <MemoryBrowser onClose={() => setMemoryOpen(false)} />}
      {authed && <ProactiveBubble />}
      <BootFailureOverlay />
      <DeveloperOverlay />
      {authed && <GatewayBooter />}
    </>
  )
}
