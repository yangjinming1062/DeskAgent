import { useStore } from '@nanostores/react'
import { useEffect, useState } from 'react'

import { useGatewayRequest } from '@/companion/boot/use-gateway-request'
import { $activeTransitionClip, $clipCatalog, type ClipMeta, computeTier, getClipAsset, updateClipCatalog } from '@/companion/clip-store'
import { $spriteEmotion, $spriteState, type SpriteEmotion, type SpriteStateName } from '@/companion/companion-store'
import { $gatewayState } from '@/shared/store/gateway'

// The companion renders the best available tier for the active scene:
//   Tier 3 - generated video clip
//   Tier 2 - multi-frame keyframe sprite sheet (cycled via CSS steps)
//   Tier 1 - portrait + state-driven procedural animation (never blank)
// Tier 1 needs no generated asset, so the companion is alive the instant a
// portrait exists — video generation cost/quotas never block startup.

const CELL = 160

// Each state and each emotion carries one presentation that both the
// procedural animation and the status badge read from, so the two views
// can't drift apart (e.g. a mood being "sag" but rendering a different
// emoji than the others in its bucket).
const STATE_PRESENTATION: Record<SpriteStateName, { proc: string; badge: string | null }> = {
  idle: { proc: 'idle', badge: null },
  speaking: { proc: 'speak', badge: '💬' },
  thinking: { proc: 'think', badge: '💭' },
  working: { proc: 'work', badge: '⚙️' },
  listening: { proc: 'listen', badge: '👂' },
  sleeping: { proc: 'sleep', badge: '💤' },
  disconnected: { proc: 'sleep', badge: '📴' },
  interacting: { proc: 'idle', badge: '✨' },
  emotional: { proc: 'idle', badge: null },
}

const EMOTION_PRESENTATION: Record<SpriteEmotion, { proc: string; badge: string }> = {
  happy: { proc: 'bounce', badge: '💛' },
  excited: { proc: 'bounce', badge: '💛' },
  playful: { proc: 'bounce', badge: '💛' },
  sad: { proc: 'sag', badge: '😔' },
  lonely: { proc: 'sag', badge: '😔' },
  bored: { proc: 'sag', badge: '💤' },
  surprised: { proc: 'pop', badge: '❓' },
  confused: { proc: 'pop', badge: '❓' },
  shy: { proc: 'shy', badge: '😊' },
  grateful: { proc: 'shy', badge: '😊' },
  proud: { proc: 'proud', badge: '⭐' },
  concerned: { proc: 'pulse', badge: '✨' },
}

function proceduralKey(state: SpriteStateName, emotion: SpriteEmotion | null): string {
  if (state === 'emotional' && emotion) {
    return EMOTION_PRESENTATION[emotion]?.proc ?? 'pulse'
  }

  return STATE_PRESENTATION[state]?.proc ?? 'idle'
}

function getStateBadge(state: SpriteStateName, emotion: SpriteEmotion | null): string | null {
  if (state === 'emotional' && emotion) {
    return EMOTION_PRESENTATION[emotion]?.badge ?? '✨'
  }

  return STATE_PRESENTATION[state]?.badge ?? null
}

export function CompanionReady() {
  const [portraitUrl, setPortraitUrl] = useState<string | null>(null)
  const [videoFailed, setVideoFailed] = useState<boolean>(false)
  const [activeTier, setActiveTier] = useState<number>(1)
  const [activeUrl, setActiveUrl] = useState<string | null>(null)
  const [activeMeta, setActiveMeta] = useState<ClipMeta | null>(null)
  const [prevTier, setPrevTier] = useState<number>(1)
  const [prevUrl, setPrevUrl] = useState<string | null>(null)
  const [fading, setFading] = useState<boolean>(false)
  const [idleVariant, setIdleVariant] = useState<string>('idle')

  const gatewayState = useStore($gatewayState)
  const spriteState = useStore($spriteState)
  const emotion = useStore($spriteEmotion)
  const transitionClip = useStore($activeTransitionClip)
  useStore($clipCatalog)
  const { requestGateway } = useGatewayRequest()
  const drowsy = gatewayState !== 'open' || spriteState === 'disconnected'

  useEffect(() => {
    if (spriteState !== 'idle' || drowsy) {
      setIdleVariant('idle')

      return
    }

    const IDLE_VARIANTS = ['idle', 'idle_look_around', 'idle_blink', 'idle_stretch']
    const nextInterval = Math.floor(Math.random() * 15000) + 10000

    const timer = setTimeout(() => {
      const available = IDLE_VARIANTS.filter(v => v === 'idle' || getClipAsset(v).tier >= 2)
      setIdleVariant(available[Math.floor(Math.random() * available.length)] ?? 'idle')
    }, nextInterval)

    return () => clearTimeout(timer)
  }, [spriteState, drowsy, activeUrl])

  const activeScene = transitionClip ?? (spriteState === 'emotional' && emotion ? emotion : spriteState === 'idle' ? idleVariant : spriteState)
  const asset = getClipAsset(activeScene)

  useEffect(() => {
    if (asset.url !== activeUrl || asset.tier !== activeTier) {
      setPrevTier(activeTier)
      setPrevUrl(activeUrl)
      setActiveTier(asset.tier)
      setActiveUrl(asset.url)
      setActiveMeta(asset.keyframe_meta)
      setFading(true)
      const timer = setTimeout(() => setFading(false), 250)

      return () => clearTimeout(timer)
    }
  }, [asset.url, asset.tier, activeUrl, activeTier, asset.keyframe_meta])

  useEffect(() => {
    setVideoFailed(false)
  }, [activeUrl])

  useEffect(() => {
    let cancelled = false
    window.deskagent
      .api<{ asset_url?: string }>({ path: '/api/companion/avatar' })
      .then(r => {
        if (!cancelled) {setPortraitUrl(r.asset_url ?? null)}
      })
      .catch(() => {})

    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    if (gatewayState !== 'open') {return}
    let cancelled = false
    void requestGateway<{ clips?: { scene: string; batch?: number; status: string; tier?: number; url: string | null; keyframe_url?: string | null; keyframe_meta?: ClipMeta | null }[] }>('avatar.list_clips', {})
      .then(res => {
        if (!cancelled && res?.clips?.length) {
          updateClipCatalog(
            res.clips.map(c => ({
              scene: c.scene,
              batch: c.batch ?? 1,
              tier: computeTier(c),
              status: c.status as 'succeeded' | 'failed' | 'pending' | 'processing' | 'queued',
              url: c.url,
              keyframe_url: c.keyframe_url ?? null,
              keyframe_meta: c.keyframe_meta ?? null,
            }))
          )
        }
      })
      .catch(() => {})

    return () => {
      cancelled = true
    }
  }, [gatewayState, requestGateway])

  const badge = drowsy && spriteState !== 'disconnected' ? '💤' : getStateBadge(spriteState, emotion)
  const procKey = proceduralKey(spriteState, emotion)
  const drowsyFilter = drowsy ? 'grayscale(0.6) brightness(0.85)' : undefined

  const showVideo = activeTier === 3 && Boolean(activeUrl) && !videoFailed
  const showSprite = !showVideo && activeTier === 2 && Boolean(activeUrl)

  return (
    <div className="companion-ready select-none" style={{ width: CELL, height: CELL }}>
      <style>{READY_CSS}</style>
      <span className="companion-glow" style={{ opacity: drowsy ? 0.2 : undefined }} />
      {showVideo && activeUrl ? (
        <div className="relative h-40 w-40 overflow-hidden rounded-full">
          {prevUrl && prevTier === 3 && fading && (
            <video autoPlay className="companion-video absolute inset-0 h-full w-full object-cover" loop muted playsInline src={prevUrl} style={{ filter: drowsyFilter }} />
          )}
          <video autoPlay className="companion-video absolute inset-0 h-full w-full object-cover" loop muted onError={() => setVideoFailed(true)} playsInline src={activeUrl} style={{ filter: drowsyFilter }} />
        </div>
      ) : showSprite && activeUrl ? (
        <KeyframeSprite cols={activeMeta?.cols ?? 4} filter={drowsyFilter} fps={activeMeta?.fps ?? 6} url={activeUrl} />
      ) : portraitUrl ? (
        <img alt="companion" className={`companion-img proc-${procKey}`} draggable={false} src={portraitUrl} style={{ filter: drowsyFilter }} />
      ) : (
        <div className="companion-ph grid place-items-center rounded-full bg-white/10 text-white/40">伙伴</div>
      )}
      {badge && <span className="companion-badge">{badge}</span>}
    </div>
  )
}

function KeyframeSprite({ url, cols, fps, filter }: { url: string; cols: number; fps: number; filter: string | undefined }) {
  // Single-row sprite strip: scale so one cell fills the 160px frame, then
  // step background-position across (cols-1) cells. steps(cols-1) shows all
  // cols frames and loops seamlessly back to the first.
  const steps = Math.max(cols - 1, 0)
  const duration = steps > 0 ? (cols / Math.max(fps, 1)).toFixed(2) : '0'
  const anim = steps > 0 ? `sprite${cols} ${duration}s steps(${steps}) infinite` : undefined

  return (
    <div
      className="companion-sprite"
      style={{
        width: CELL,
        height: CELL,
        backgroundImage: `url(${url})`,
        backgroundSize: `${cols * CELL}px ${CELL}px`,
        backgroundRepeat: 'no-repeat',
        animation: anim,
        filter,
      }}
    />
  )
}

const READY_CSS = `
@keyframes procBreathe { 0%,100%{transform:scale(1)} 50%{transform:scale(1.03)} }
@keyframes procSpeak { 0%,100%{transform:translateY(0) scale(1.01)} 50%{transform:translateY(-3px) scale(1.02)} }
@keyframes procThink { 0%,100%{transform:rotate(0deg)} 25%{transform:rotate(-2deg)} 75%{transform:rotate(2deg)} }
@keyframes procSleep { 0%,100%{transform:translateY(0) scale(1)} 50%{transform:translateY(2px) scale(0.99)} }
@keyframes procWork { 0%,100%{transform:scale(1)} 50%{transform:scale(1.015)} }
@keyframes procListen { 0%,100%{transform:translateY(0)} 50%{transform:translateY(-1.5px)} }
@keyframes procBounce { 0%,100%{transform:translateY(0) scale(1)} 40%{transform:translateY(-6px) scale(1.04)} 60%{transform:translateY(-6px) scale(1.04)} }
@keyframes procSag { 0%,100%{transform:translateY(0) rotate(0)} 50%{transform:translateY(2px) rotate(-1.5deg)} }
@keyframes procPop { 0%{transform:scale(1)} 40%{transform:scale(1.08)} 100%{transform:scale(1)} }
@keyframes procShy { 0%,100%{transform:translateX(0) rotate(0)} 50%{transform:translateX(-3px) rotate(-2deg)} }
@keyframes procProud { 0%,100%{transform:scale(1)} 50%{transform:scale(1.04) translateY(-2px)} }
@keyframes procPulse { 0%,100%{transform:scale(1)} 50%{transform:scale(1.03)} }
@keyframes companionGlow { 0%,100%{opacity:.4} 50%{opacity:.65} }
@keyframes companionBadge { 0%,100%{opacity:.8;transform:translateY(0)} 50%{opacity:1;transform:translateY(-3px)} }
@keyframes sprite2 { to { background-position-x: -160px } }
@keyframes sprite3 { to { background-position-x: -320px } }
@keyframes sprite4 { to { background-position-x: -480px } }
@keyframes sprite5 { to { background-position-x: -640px } }
@keyframes sprite6 { to { background-position-x: -800px } }
.companion-ready { position: relative; display: grid; place-items: center; }
.companion-img, .companion-ph, .companion-sprite { width: 160px; height: 160px; border-radius: 9999px; object-fit: cover; }
.companion-img.proc-idle { animation: procBreathe 3.6s ease-in-out infinite; }
.companion-img.proc-speak { animation: procSpeak 0.5s ease-in-out infinite; }
.companion-img.proc-think { animation: procThink 3s ease-in-out infinite; }
.companion-img.proc-sleep { animation: procSleep 5s ease-in-out infinite; }
.companion-img.proc-work { animation: procWork 2.4s ease-in-out infinite; }
.companion-img.proc-listen { animation: procListen 2.2s ease-in-out infinite; }
.companion-img.proc-bounce { animation: procBounce 0.9s ease-in-out infinite; }
.companion-img.proc-sag { animation: procSag 4s ease-in-out infinite; }
.companion-img.proc-pop { animation: procPop 1.2s ease-in-out infinite; }
.companion-img.proc-shy { animation: procShy 3.2s ease-in-out infinite; }
.companion-img.proc-proud { animation: procProud 2.8s ease-in-out infinite; }
.companion-img.proc-pulse { animation: procPulse 2s ease-in-out infinite; }
.companion-video { animation: procBreathe 3.6s ease-in-out infinite; }
.companion-glow { position: absolute; width: 170%; height: 170%; border-radius: 9999px; background: radial-gradient(closest-side, rgba(255,209,102,0.35), transparent 70%); filter: blur(8px); animation: companionGlow 3.4s ease-in-out infinite; pointer-events: none; }
.companion-badge { position: absolute; top: 4%; right: 18%; font-size: 16px; animation: companionBadge 2.6s ease-in-out infinite; text-shadow: 0 0 6px rgba(0,0,0,0.5); pointer-events: none; }
`
