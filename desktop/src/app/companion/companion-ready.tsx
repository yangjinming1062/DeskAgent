import { useEffect, useState } from 'react'

// The hatched companion at idle — the Backend portrait rendered with a gentle
// breathing animation. No WebM clips exist yet (Desktop-first MVP), so this is
// the code-rendered idle until clip generation ships; it's the same fallback
// the "never blank" invariant (design.md §11#9) guarantees. Tap/chat arrives in
// Slice 3 (SpriteStage routes taps here).
export function CompanionReady() {
  const [url, setUrl] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    window.deskagent
      .api<{ asset_url?: string }>({ path: '/api/companion/avatar' })
      .then(r => {
        if (!cancelled) setUrl(r.asset_url ?? null)
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
      <span className="companion-glow" />
      {url ? (
        <img src={url} alt="companion" className="companion-img" draggable={false} />
      ) : (
        <div className="companion-ph grid place-items-center rounded-full bg-white/10 text-white/40">伙伴</div>
      )}
    </div>
  )
}

const READY_CSS = `
@keyframes companionBreathe { 0%,100%{transform:scale(1)} 50%{transform:scale(1.03)} }
@keyframes companionGlow { 0%,100%{opacity:.4} 50%{opacity:.65} }
.companion-ready { position: relative; display: grid; place-items: center; }
.companion-img, .companion-ph { width: 160px; height: 160px; border-radius: 9999px; object-fit: cover; animation: companionBreathe 3.6s ease-in-out infinite; }
.companion-glow { position: absolute; width: 170%; height: 170%; border-radius: 9999px; background: radial-gradient(closest-side, rgba(255,209,102,0.35), transparent 70%); filter: blur(8px); animation: companionGlow 3.4s ease-in-out infinite; }
`
