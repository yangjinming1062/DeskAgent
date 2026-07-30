import { useStore } from '@nanostores/react'
import { useEffect, useState } from 'react'

import { $gatewayState } from '@/shared/store/gateway'

// The hatched companion at idle — the Backend portrait rendered with a gentle
// breathing animation. No WebM clips exist yet (Desktop-first MVP), so this is
// the code-rendered idle until clip generation ships; it's the same fallback
// the "never blank" invariant (design.md §11#9) guarantees. When the gateway is
// down the companion looks drowsy (plan.md §4.5 DISCONNECTED) — basic MVP
// level; foreground/background grace arrives in phase 2.
export function CompanionReady() {
  const [url, setUrl] = useState<string | null>(null)
  const gatewayState = useStore($gatewayState)
  const drowsy = gatewayState !== 'open'

  useEffect(() => {
    let cancelled = false
    window.deskagent
      .api<{ asset_url?: string }>({ path: '/api/companion/avatar' })
      .then(r => {
        if (!cancelled) {setUrl(r.asset_url ?? null)}
      })
      .catch(() => {
        /* stay on placeholder — companion never "blank" */
      })

    return () => {
      cancelled = true
    }
  }, [])

  return (
    <div className="companion-ready select-none" style={{ width: 160, height: 160 }}>
      <style>{READY_CSS}</style>
      <span className="companion-glow" style={{ opacity: drowsy ? 0.2 : undefined }} />
      {url ? (
        <img
          alt="companion"
          className="companion-img"
          draggable={false}
          src={url}
          style={{ filter: drowsy ? 'grayscale(0.6) brightness(0.85)' : undefined }}
        />
      ) : (
        <div className="companion-ph grid place-items-center rounded-full bg-white/10 text-white/40">伙伴</div>
      )}
      {drowsy && <span className="companion-zzz">z</span>}
    </div>
  )
}

const READY_CSS = `
@keyframes companionBreathe { 0%,100%{transform:scale(1)} 50%{transform:scale(1.03)} }
@keyframes companionGlow { 0%,100%{opacity:.4} 50%{opacity:.65} }
@keyframes companionZzz { 0%,100%{opacity:.7;transform:translateY(0)} 50%{opacity:1;transform:translateY(-3px)} }
.companion-ready { position: relative; display: grid; place-items: center; }
.companion-img, .companion-ph { width: 160px; height: 160px; border-radius: 9999px; object-fit: cover; animation: companionBreathe 3.6s ease-in-out infinite; }
.companion-glow { position: absolute; width: 170%; height: 170%; border-radius: 9999px; background: radial-gradient(closest-side, rgba(255,209,102,0.35), transparent 70%); filter: blur(8px); animation: companionGlow 3.4s ease-in-out infinite; }
.companion-zzz { position: absolute; top: 4%; right: 18%; font-size: 14px; color: #9a8fb4; animation: companionZzz 2.6s ease-in-out infinite; }
`
