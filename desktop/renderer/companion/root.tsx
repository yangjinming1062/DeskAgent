import { useStore } from '@nanostores/react'
import { useEffect, useRef, useState } from 'react'

import { startActivityMonitor } from '@/companion/activity'
import { useGatewayBoot } from '@/companion/boot/use-gateway-boot'
import { useGatewayRequest } from '@/companion/boot/use-gateway-request'
import { $chatOpen, setChatOpen } from '@/companion/chat-store'
import { $companionLifecycle, checkBedtimeAndAutoSleep, reportUserActivity, setCompanionLifecycle, wakeUpFromSleep } from '@/companion/companion-store'
import { hydratePersona } from '@/companion/persona-store'
import { $auth, applyAuthBroadcast, hydrateAuth, logout } from '@/shared/store/auth'
import { $gatewayState } from '@/shared/store/gateway'
import { notify } from '@/shared/store/notifications'
import { strings } from '@/shared/strings'

import { ChatDock } from './chat-dock'
import { DeveloperOverlay } from './developer-overlay'
import { handleCompanionEvent } from './events'
import { handlePokeInteraction } from './interaction'
import { OnboardingFlow } from './onboarding/onboarding-flow'
import { speakProactive } from './proactive/proactive'
import { ProactiveBubble } from './proactive/proactive-bubble'
import { CompanionSettings } from './settings-overlay'
import { CompanionReady } from './sprite/companion-ready'
import { SpriteContextMenu } from './sprite/context-menu'
import { Egg, type EggMode } from './sprite/egg'
import { SpriteStage } from './sprite/sprite-stage'
import { VoiceCallDock } from './voice-call-dock'
import { checkCompanionVoiceValidity } from './voice-validity'

const HATCH_AT = 5

// Boots the gateway as a mount effect — so it only runs while authenticated.
// When $auth flips back to unauthenticated (logout/expiry) this unmounts and
// useGatewayBoot's cleanup tears the WS down. handleGatewayEvent dispatches the
// streaming chat frames onto the chat store + state machine (events.ts).
function GatewayBooter() {
  useGatewayBoot({
    handleGatewayEvent: handleCompanionEvent,
    onConnectionReady: () => {},
    onGatewayReady: () => {}
  })

  return null
}

export function CompanionRoot() {
  const auth = useStore($auth)
  const gatewayState = useStore($gatewayState)
  const lifecycle = useStore($companionLifecycle)
  const chatOpen = useStore($chatOpen)
  const [cracks, setCracks] = useState(0)
  const [voiceCallOpen, setVoiceCallOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [contextMenuPos, setContextMenuPos] = useState<{ x: number; y: number } | null>(null)
  const { requestGateway } = useGatewayRequest()
  const validityCheckedRef = useRef(false)

  useEffect(() => {
    void hydrateAuth()
  }, [])

  useEffect(() => {
    const off = window.deskagent.onAuthChanged(payload => applyAuthBroadcast(payload))

    return () => off()
  }, [])

  useEffect(() => {
    const off = window.deskagent.onSessionExpired(() => void logout())

    return () => off()
  }, [])

  // Dev-only: inject a test proactive message (Ctrl+Shift+P) to exercise the
  // companion.message receiver + bubble + TTS without the Backend send_message
  // path. Stripped in production builds.
  useEffect(() => {
    if (import.meta.env.PROD) {return}

    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey && e.shiftKey && (e.key === 'P' || e.key === 'p')) {
        e.preventDefault()
        void speakProactive('（测试）嘿，休息一下眼睛吧～')
      }
    }

    window.addEventListener('keydown', onKey)

    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // On auth, route by persona completeness: incomplete → onboarding, complete
  // → ready. While resolving we stay in `unauthed-egg` so the waking egg shows.
  useEffect(() => {
    if (auth.kind !== 'authenticated') {
      setCompanionLifecycle('unauthed-egg')

      return
    }

    let cancelled = false
    setCompanionLifecycle('unauthed-egg')
    window.deskagent
      .api<{ is_complete?: boolean }>({ path: '/api/companion/persona' })
      .then(p => {
        if (!cancelled) {setCompanionLifecycle(p?.is_complete ? 'ready' : 'onboarding')}
      })
      .catch(() => {
        if (!cancelled) {setCompanionLifecycle('onboarding')}
      })

    return () => {
      cancelled = true
    }
  }, [auth.kind])

  const authed = auth.kind === 'authenticated'
  const resolving = authed && lifecycle === 'unauthed-egg'
  const showEgg = !authed || resolving
  const showOnboarding = authed && lifecycle === 'onboarding'
  const showReady = authed && lifecycle === 'ready'
  const mode: EggMode = !authed ? 'teaser' : gatewayState === 'open' ? 'awake' : 'drowsy'

  useEffect(() => {
    if (lifecycle !== 'ready') {return}
    checkBedtimeAndAutoSleep()

    const timer = setInterval(() => {
      checkBedtimeAndAutoSleep()
    }, 60000)

    const onKey = () => reportUserActivity()
    window.addEventListener('keydown', onKey)

    const stopActivity = startActivityMonitor()
    void hydratePersona()

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
    if (lifecycle !== 'ready' || gatewayState !== 'open' || validityCheckedRef.current) {return}
    validityCheckedRef.current = true

    void checkCompanionVoiceValidity(requestGateway).then(result => {
      if (result.valid) {return}
      notify({
        kind: 'warning',
        title: strings.notifications.voice.invalidTitle,
        message: strings.notifications.voice.invalidMessage(result.name),
        action: { label: strings.notifications.voice.invalidAction, onClick: () => setSettingsOpen(true) }
      })
    })
  }, [lifecycle, gatewayState, requestGateway])

  // Reset the crack counter whenever the user transitions back to the
  // pre-auth / unauthed-egg state. P0-2 (runtime audit): the counter
  // was previously monotonically increasing across the entire session,
  // so a user who logged out had to tap the egg 5 more times to summon
  // login again. Resetting on logout restores the "first-tap counts"
  // promise of the egg-teaser design.
  useEffect(() => {
    if (auth.kind !== 'authenticated') {
      setCracks(0)
    }
  }, [auth.kind])

  const onTap = () => {
    if (showReady) {
      wakeUpFromSleep()
      handlePokeInteraction()

      return
    }

    // Pre-auth: each tap cracks the egg; 5 cracks shatter it and summon login.
    if (authed) {return}

    if (cracks >= HATCH_AT) {
      void window.deskagent.showToolWindow()

      return
    }

    const next = cracks + 1
    setCracks(next)

    if (next >= HATCH_AT) {void window.deskagent.showToolWindow()}
  }

  // Plan §4.3: double-tap the ready companion to open Chat. Single-tap poke
  // reactions (LLM-generated) arrive in a later slice.
  const onDoubleTap = () => {
    if (showReady) {setChatOpen(true)}
  }

  return (
    <>
      {showOnboarding && <OnboardingFlow onCompleted={() => setCompanionLifecycle('ready')} />}
      <SpriteStage
        onContextMenu={e => {
          if (showReady) {
            setContextMenuPos({ x: e.clientX, y: e.clientY })
          }
        }}
        onDoubleTap={onDoubleTap}
        onTap={onTap}
      >
        {showReady ? <CompanionReady /> : showEgg ? <Egg cracks={cracks} mode={mode} /> : null}
      </SpriteStage>
      {showReady && contextMenuPos && (
        <SpriteContextMenu
          onClose={() => setContextMenuPos(null)}
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
      {showReady && <ProactiveBubble />}
      <DeveloperOverlay />
      {authed && <GatewayBooter />}
    </>
  )
}
