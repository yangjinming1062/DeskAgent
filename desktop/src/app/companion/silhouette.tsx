// The glowing, not-yet-formed silhouette that conducts onboarding (plan.md
// §3.2). `clarity` (0..1) rises as the user answers — the silhouette sharpens
// and brightens so the user feels it "becoming what they imagined". Code-
// rendered (no art asset); the real companion is the Backend portrait.

interface SilhouetteProps {
  clarity: number
  size?: number
  spin?: boolean
}

export function Silhouette({ clarity, size = 180, spin = false }: SilhouetteProps) {
  const c = Math.max(0, Math.min(1, clarity))
  const blur = (1 - c) * 7
  const fillOpacity = 0.35 + c * 0.6

  return (
    <div className={`sil-root select-none ${spin ? 'sil-spin' : 'sil-float'}`} style={{ width: size, height: size }}>
      <style>{SIL_CSS}</style>
      <span className="sil-glow" style={{ opacity: 0.25 + c * 0.55 }} />
      <svg
        viewBox="0 0 200 200"
        width={size}
        height={size}
        style={{ filter: `blur(${blur.toFixed(2)}px)`, opacity: fillOpacity }}
      >
        <defs>
          <radialGradient id="silFill" cx="50%" cy="38%" r="62%">
            <stop offset="0%" stopColor="#fff6c2" />
            <stop offset="70%" stopColor="#f0c949" stopOpacity="0.95" />
            <stop offset="100%" stopColor="#e0a138" stopOpacity="0.75" />
          </radialGradient>
        </defs>
        <circle cx="100" cy="58" r="30" fill="url(#silFill)" />
        <path d="M100 92 C66 92 52 134 52 176 L148 176 C148 134 134 92 100 92 Z" fill="url(#silFill)" />
      </svg>
    </div>
  )
}

const SIL_CSS = `
@keyframes silFloat { 0%,100%{transform:translateY(0)} 50%{transform:translateY(-5px)} }
@keyframes silSpin { to { transform: rotate(360deg) } }
@keyframes silGlowPulse { 0%,100%{opacity:var(--g,0.4)} 50%{opacity:calc(var(--g,0.4)*1.4)} }
.sil-root { position: relative; display: grid; place-items: center; }
.sil-float { animation: silFloat 3.6s ease-in-out infinite; }
.sil-spin { animation: silSpin 6s linear infinite; }
.sil-glow {
  position: absolute; width: 170%; height: 170%; border-radius: 9999px;
  background: radial-gradient(closest-side, rgba(255,209,102,0.5), transparent 70%);
  filter: blur(10px); animation: silGlowPulse 3s ease-in-out infinite;
}
`
