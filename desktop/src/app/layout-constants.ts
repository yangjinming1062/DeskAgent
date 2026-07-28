// Responsive horizontal gutter for primary content bodies (settings right
// side, skills). Ratio-based so it scales with the window, but clamped so it
// never collapses on narrow widths or runs away on ultrawide displays.
// Headers/tabs intentionally keep their own tighter padding.
//
// NOTE: must stay a literal string — Tailwind's scanner only picks up
// complete class names, so do not build it via template interpolation.
export const PAGE_INSET_X = 'px-[clamp(1.25rem,4vw,4rem)]'
