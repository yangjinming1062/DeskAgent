// Code-rendered default companion visual for the pre-generation phases: the
// egg (plan.md §3.1) and its waking/drowsy states. No binary art dependency —
// the real companion is a Backend-generated portrait + WebM clips (Slice 3+);
// until then this SVG is what the user sees, and it doubles as the
// clip-not-ready fallback (ARCHITECTURE.md §11#9).

const EGG_SIZE = 160

export type EggMode = 'teaser' | 'drowsy' | 'awake'

interface EggProps {
  cracks: number
  mode: EggMode
}

// Crack polylines, revealed one per accumulated poke. Five pokes shatter the
// shell (plan.md §3.1: enough effort to feel earned, not annoying).
const CRACK_PATHS = [
  'M80 40 L72 62 L84 70 L74 88',
  'M96 50 L104 68 L92 76 L100 92',
  'M64 70 L56 84 L68 90 L60 104',
  'M110 80 L118 94 L106 100'
]

export function Egg({ cracks, mode }: EggProps) {
  const shattered = cracks >= 5
  const wobble = mode === 'drowsy' ? 'egg-drowsy' : mode === 'teaser' ? 'egg-breathe' : 'egg-awake'

  return (
    <div className="select-none" style={{ width: EGG_SIZE, height: EGG_SIZE + 24 }}>
      <style>{KEYFRAMES}</style>
      <div
        aria-label="DeskAgent egg"
        className={`egg-root ${wobble} relative grid place-items-center`}
        role="img"
        style={{ width: EGG_SIZE, height: EGG_SIZE }}
      >
        {/* Ambient glow — brightens as the companion "comes to". */}
        <span
          className="egg-glow"
          style={{
            opacity: mode === 'awake' ? 0.85 : mode === 'drowsy' ? 0.25 : 0.45
          }}
        />
        <svg
          className="egg-shell"
          height={EGG_SIZE}
          style={{ transform: shattered ? 'scale(0.92)' : undefined }}
          viewBox="0 0 160 160"
          width={EGG_SIZE}
        >
          <defs>
            <radialGradient cx="42%" cy="36%" id="eggFill" r="72%">
              <stop offset="0%" stopColor="#fffdf6" />
              <stop offset="60%" stopColor="#f6efe0" />
              <stop offset="100%" stopColor="#e6dcc4" />
            </radialGradient>
            <radialGradient cx="50%" cy="50%" id="coreGlow" r="50%">
              <stop offset="0%" stopColor="#fff3b0" />
              <stop offset="70%" stopColor="#ffd166" stopOpacity="0.55" />
              <stop offset="100%" stopColor="#ffd166" stopOpacity="0" />
            </radialGradient>
          </defs>

          {/* Once shattered, a warm core glows through the split shell. */}
          {shattered && <circle cx="80" cy="82" fill="url(#coreGlow)" r="42" />}

          <path
            d="M80 18 C120 18 138 60 138 92 C138 124 112 146 80 146 C48 146 22 124 22 92 C22 60 40 18 80 18 Z"
            fill="url(#eggFill)"
            stroke="#d8cdb0"
            strokeWidth="1.5"
          />

          {/* Cracks accumulate; on shatter the shell halves part slightly. */}
          <g fill="none" opacity={shattered ? 0.9 : 0.7} stroke="#b9a982" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.6">
            {CRACK_PATHS.slice(0, Math.min(cracks, CRACK_PATHS.length)).map((d, i) => (
              <path d={d} key={i} />
            ))}
            {shattered && <path d="M80 18 L74 60 L86 88 L72 120 L80 146" strokeWidth="2" />}
          </g>
        </svg>

        {mode === 'drowsy' && <span className="egg-zzz">z</span>}
      </div>
    </div>
  )
}

const KEYFRAMES = `
@keyframes eggBreathe {
  0%, 100% { transform: scale(1); }
  50% { transform: scale(1.035); }
}
@keyframes eggAwake {
  0%, 100% { transform: translateY(0) scale(1); }
  50% { transform: translateY(-4px) scale(1.015); }
}
@keyframes eggDrowsy {
  0%, 100% { transform: rotate(-3deg); }
  50% { transform: rotate(3deg); }
}
@keyframes eggGlowPulse {
  0%, 100% { opacity: var(--glow, 0.45); }
  50% { opacity: calc(var(--glow, 0.45) * 1.5); }
}
.egg-root { background: transparent; border: 0; padding: 0; cursor: pointer; }
.egg-breathe { animation: eggBreathe 3.4s ease-in-out infinite; }
.egg-awake { animation: eggAwake 2.8s ease-in-out infinite; }
.egg-drowsy { animation: eggDrowsy 5s ease-in-out infinite; }
.egg-glow {
  position: absolute; width: 150%; height: 150%; border-radius: 9999px;
  background: radial-gradient(closest-side, rgba(255,209,102,0.55), transparent 70%);
  filter: blur(6px); animation: eggGlowPulse 3.4s ease-in-out infinite; pointer-events: none;
}
.egg-zzz {
  position: absolute; top: 6%; right: 18%; font-size: 14px; color: #9a8fb4;
  opacity: 0.8; animation: eggBreathe 3s ease-in-out infinite; pointer-events: none;
}
`
