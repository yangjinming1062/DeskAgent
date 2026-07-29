import { useStore } from '@nanostores/react'
import { useEffect, useState } from 'react'

import { useGatewayBoot } from '@/app/gateway/hooks/use-gateway-boot'
import { $auth, applyAuthBroadcast, hydrateAuth, logout } from '@/store/auth'
import { $gatewayState } from '@/store/gateway'
import { $companionLifecycle, setCompanionLifecycle } from '@/store/companion'

import { CompanionReady } from './companion-ready'
import { Egg, type EggMode } from './egg'
import { OnboardingFlow } from './onboarding-flow'
import { SpriteStage } from './sprite-stage'

const HATCH_AT = 5

// Boots the gateway as a mount effect — so it only runs while authenticated.
// When $auth flips back to unauthenticated (logout/expiry) this unmounts and
// useGatewayBoot's cleanup tears the WS down. The empty handleGatewayEvent is
// the designated companion graft point (chat / tool / affect dispatch lands in
// Slice 3+); today it only keeps the connection alive.
function GatewayBooter() {
  useGatewayBoot({
    handleGatewayEvent: () => {
      /* companion event dispatch — Slice 3 */
    },
    onConnectionReady: () => {},
    onGatewayReady: () => {}
  })
  return null
}

export function CompanionRoot() {
  const auth = useStore($auth)
  const gatewayState = useStore($gatewayState)
  const lifecycle = useStore($companionLifecycle)
  const [cracks, setCracks] = useState(0)

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
        if (!cancelled) setCompanionLifecycle(p?.is_complete ? 'ready' : 'onboarding')
      })
      .catch(() => {
        if (!cancelled) setCompanionLifecycle('onboarding')
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

  const onTap = () => {
    // Pre-auth: each tap cracks the egg; 5 cracks shatter it and summon login.
    // Post-auth the egg is replaced by onboarding / the ready companion, so taps
    // are a no-op here until Slice 3 (poke reactions / open chat).
    if (authed) return
    if (cracks >= HATCH_AT) {
      void window.deskagent.showToolWindow()
      return
    }
    const next = cracks + 1
    setCracks(next)
    if (next >= HATCH_AT) void window.deskagent.showToolWindow()
  }

  return (
    <>
      {showOnboarding && <OnboardingFlow onCompleted={() => setCompanionLifecycle('ready')} />}
      {(showEgg || showReady) && (
        <SpriteStage onTap={onTap}>
          {showReady ? <CompanionReady /> : <Egg cracks={cracks} mode={mode} />}
        </SpriteStage>
      )}
      {authed && <GatewayBooter />}
    </>
  )
}
