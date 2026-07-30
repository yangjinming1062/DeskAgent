import { useStore } from '@nanostores/react'
import { useEffect, useState } from 'react'

import { $activeTransitionClip, $clipCatalog, getClipUrlForScene } from '@/companion/clip-store'
import { $spriteEmotion, $spriteState, type SpriteEmotion, type SpriteStateName } from '@/companion/companion-store'
import { $gatewayState } from '@/shared/store/gateway'

function getStateBadge(state: SpriteStateName, emotion: SpriteEmotion | null): string | null {
  if (state === 'disconnected') return '🔌'
  if (state === 'sleeping') return '💤'
  if (state === 'working') return '⚙️'
  if (state === 'thinking') return '💭'
  if (state === 'listening') return '🎧'
  if (state === 'speaking') return '💬'
  if (state === 'interacting') return '✨'
  if (state === 'emotional' && emotion) {
    switch (emotion) {
      case 'happy':
      case 'excited':
      case 'playful':
        return '❤️'
      case 'sad':
      case 'lonely':
        return '💧'
      case 'surprised':
      case 'confused':
        return '❓'
      case 'shy':
      case 'grateful':
        return '🌸'
      case 'proud':
        return '🌟'
      case 'bored':
        return '💤'
      default:
        return '✨'
    }
  }
  return null
}

export function CompanionReady() {
  const [portraitUrl, setPortraitUrl] = useState<string | null>(null)
  const [videoFailed, setVideoFailed] = useState<boolean>(false)
  const [activeUrl, setActiveUrl] = useState<string | null>(null)
  const [prevUrl, setPrevUrl] = useState<string | null>(null)
  const [fading, setFading] = useState<boolean>(false)

  const [idleVariant, setIdleVariant] = useState<string>('idle')

  const gatewayState = useStore($gatewayState)
  const spriteState = useStore($spriteState)
  const emotion = useStore($spriteEmotion)
  const transitionClip = useStore($activeTransitionClip)
  const clipCatalog = useStore($clipCatalog)
  const drowsy = gatewayState !== 'open' || spriteState === 'disconnected'

  // Random micro-action timer for IDLE state (10s-25s)
  useEffect(() => {
    if (spriteState !== 'idle' || drowsy) {
      setIdleVariant('idle')
      return
    }

    const IDLE_VARIANTS = ['idle', 'idle_look_around', 'idle_blink', 'idle_stretch']
    const nextInterval = Math.floor(Math.random() * 15000) + 10000

    const timer = setTimeout(() => {
      const available = IDLE_VARIANTS.filter(scene => scene === 'idle' || Boolean(getClipUrlForScene(scene)))
      const picked = available[Math.floor(Math.random() * available.length)]
      setIdleVariant(picked)
    }, nextInterval)

    return () => clearTimeout(timer)
  }, [spriteState, drowsy, activeUrl])

  // Determine active scene (transition clip has top priority if active)
  const activeScene = transitionClip
    ? transitionClip
    : spriteState === 'emotional' && emotion
      ? emotion
      : spriteState === 'idle'
        ? idleVariant
        : spriteState
  const clipUrl = getClipUrlForScene(activeScene) ?? getClipUrlForScene('idle')

  useEffect(() => {
    if (clipUrl !== activeUrl) {
      setPrevUrl(activeUrl)
      setActiveUrl(clipUrl)
      setFading(true)
      const timer = setTimeout(() => setFading(false), 250)
      return () => clearTimeout(timer)
    }
  }, [clipUrl, activeUrl])

  useEffect(() => {
    setVideoFailed(false)
  }, [clipUrl])

  useEffect(() => {
    let cancelled = false
    window.deskagent
      .api<{ asset_url?: string }>({ path: '/api/companion/avatar' })
      .then(r => {
        if (!cancelled) {
          setPortraitUrl(r.asset_url ?? null)
        }
      })
      .catch(() => {
        /* stay on placeholder — companion never "blank" */
      })

    return () => {
      cancelled = true
    }
  }, [])

  const badge = drowsy && spriteState !== 'disconnected' ? '💤' : getStateBadge(spriteState, emotion)
  const showVideo = Boolean(activeUrl) && !videoFailed

  return (
    <div className="companion-ready select-none" style={{ width: 160, height: 160 }}>
      <style>{READY_CSS}</style>
      <span className="companion-glow" style={{ opacity: drowsy ? 0.2 : undefined }} />
      {showVideo && activeUrl ? (
        <div className="relative h-40 w-40 overflow-hidden rounded-full">
          {prevUrl && fading && (
            <video
              autoPlay
              className="companion-video absolute inset-0 h-full w-full object-cover transition-opacity duration-250 opacity-0"
              loop
              muted
              playsInline
              src={prevUrl}
              style={{ filter: drowsy ? 'grayscale(0.6) brightness(0.85)' : undefined }}
            />
          )}
          <video
            autoPlay
            className={`companion-video absolute inset-0 h-full w-full object-cover transition-opacity duration-250 ${
              fading ? 'opacity-100' : 'opacity-100'
            }`}
            loop
            muted
            onError={() => setVideoFailed(true)}
            playsInline
            src={activeUrl}
            style={{ filter: drowsy ? 'grayscale(0.6) brightness(0.85)' : undefined }}
          />
        </div>
      ) : portraitUrl ? (
        <img
          alt="companion"
          className="companion-img"
          draggable={false}
          src={portraitUrl}
          style={{ filter: drowsy ? 'grayscale(0.6) brightness(0.85)' : undefined }}
        />
      ) : (
        <div className="companion-ph grid place-items-center rounded-full bg-white/10 text-white/40">伙伴</div>
      )}
      {badge && <span className="companion-badge">{badge}</span>}
    </div>
  )
}

const READY_CSS = `
@keyframes companionBreathe { 0%,100%{transform:scale(1)} 50%{transform:scale(1.03)} }
@keyframes companionGlow { 0%,100%{opacity:.4} 50%{opacity:.65} }
@keyframes companionBadge { 0%,100%{opacity:.8;transform:translateY(0)} 50%{opacity:1;transform:translateY(-3px)} }
.companion-ready { position: relative; display: grid; place-items: center; }
.companion-img, .companion-ph { width: 160px; height: 160px; border-radius: 9999px; object-fit: cover; animation: companionBreathe 3.6s ease-in-out infinite; }
.companion-video { animation: companionBreathe 3.6s ease-in-out infinite; }
.companion-glow { position: absolute; width: 170%; height: 170%; border-radius: 9999px; background: radial-gradient(closest-side, rgba(255,209,102,0.35), transparent 70%); filter: blur(8px); animation: companionGlow 3.4s ease-in-out infinite; }
.companion-badge { position: absolute; top: 4%; right: 18%; font-size: 16px; animation: companionBadge 2.6s ease-in-out infinite; text-shadow: 0 0 6px rgba(0,0,0,0.5); }
`

