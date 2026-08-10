interface HatchOverlayProps {
  active: boolean
}

/**
 * Visual overlay anchor during hatching sequence completion.
 */
export function HatchOverlay({ active }: HatchOverlayProps) {
  if (!active) return null

  return (
    <div
      className="pointer-events-none absolute -inset-8 rounded-full bg-radial from-amber-200/35 via-amber-100/10 to-transparent opacity-0 animate-warm-glow"
      aria-hidden="true"
    />
  )
}
