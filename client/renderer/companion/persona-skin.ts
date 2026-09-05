import { atom } from 'nanostores'

import { $portraitUrl } from '@/companion/portrait-store'
import { log } from '@/shared/lib/log'
import { $theme } from '@/shared/store/theme'

export interface PersonaSkin {
  highlight: string
  primary: string
  secondary: string
}

const DEFAULT_SKIN: PersonaSkin = {
  highlight: '#d8c2a8',
  primary: '#8aa0c8',
  secondary: '#5b6f8f'
}

export const $personaSkin = atom<PersonaSkin>(DEFAULT_SKIN)

const SKIN_TRANSITION_MS = 1500

interface RGB {
  b: number
  g: number
  r: number
}

interface HSL {
  h: number
  l: number
  s: number
}

interface Bucket {
  b: number
  count: number
  g: number
  l: number
  r: number
  weight: number
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value))
}

function rgbToHex({ b, g, r }: RGB): string {
  return `#${[r, g, b].map(v => clamp(Math.round(v), 0, 255).toString(16).padStart(2, '0')).join('')}`
}

function hexToRgb(hex: string): RGB | null {
  const match = /^#?([0-9a-f]{6})$/i.exec(hex.trim())

  if (!match) {
    return null
  }

  const intVal = Number.parseInt(match[1], 16)

  return {
    b: intVal & 0xff,
    g: (intVal >> 8) & 0xff,
    r: (intVal >> 16) & 0xff
  }
}

function rgbToHsl({ b, g, r }: RGB): HSL {
  const rn = r / 255
  const gn = g / 255
  const bn = b / 255
  const max = Math.max(rn, gn, bn)
  const min = Math.min(rn, gn, bn)
  const l = (max + min) / 2
  let h = 0
  let s = 0

  if (max !== min) {
    const d = max - min
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min)

    switch (max) {
      case rn:
        h = ((gn - bn) / d + (gn < bn ? 6 : 0)) * 60

        break

      case gn:
        h = ((bn - rn) / d + 2) * 60

        break

      default:
        h = ((rn - gn) / d + 4) * 60
    }
  }

  return { h, l, s }
}

function hslToRgb({ h, l, s }: HSL): RGB {
  if (s === 0) {
    const v = Math.round(l * 255)

    return { b: v, g: v, r: v }
  }

  const hue2rgb = (p: number, q: number, t: number): number => {
    let tt = t

    if (tt < 0) {
      tt += 1
    }

    if (tt > 1) {
      tt -= 1
    }

    if (tt < 1 / 6) {
      return p + (q - p) * 6 * tt
    }

    if (tt < 1 / 2) {
      return q
    }

    if (tt < 2 / 3) {
      return p + (q - p) * (2 / 3 - tt) * 6
    }

    return p
  }

  const q = l < 0.5 ? l * (1 + s) : l + s - l * s
  const p = 2 * l - q
  const hk = (((h % 360) + 360) % 360) / 360

  return {
    b: Math.round(hue2rgb(p, q, hk - 1 / 3) * 255),
    g: Math.round(hue2rgb(p, q, hk) * 255),
    r: Math.round(hue2rgb(p, q, hk + 1 / 3) * 255)
  }
}

function sanitizeHsl(hsl: HSL): HSL {
  return {
    h: hsl.h,
    l: clamp(hsl.l, 0.35, 0.65),
    s: Math.min(hsl.s, 0.55)
  }
}

function blendWithDefault(skin: RGB, defaultHex: string, ratio: number): RGB {
  const fallback = hexToRgb(defaultHex) ?? { b: 138, g: 160, r: 200 }
  const mix = (a: number, b: number): number => a * (1 - ratio) + b * ratio

  return {
    b: Math.round(mix(skin.b, fallback.b)),
    g: Math.round(mix(skin.g, fallback.g)),
    r: Math.round(mix(skin.r, fallback.r))
  }
}

function quantizeHsl(h: number, s: number, l: number): string {
  const hk = Math.floor(h / 18)
  const sk = s > 0.25 ? 1 : 0
  const lk = Math.floor(l * 4)

  return `${hk}-${sk}-${lk}`
}

function bucketToRgb(bucket: Bucket): RGB {
  return {
    b: bucket.b / bucket.weight,
    g: bucket.g / bucket.weight,
    r: bucket.r / bucket.weight
  }
}

function extractPalette(image: HTMLImageElement): { highlight: RGB; primary: RGB; secondary: RGB } | null {
  if (typeof document === 'undefined') {
    return null
  }

  const canvas = document.createElement('canvas')
  const size = 64
  canvas.width = size
  canvas.height = size
  const ctx = canvas.getContext('2d', { willReadFrequently: true })

  if (!ctx) {
    return null
  }

  ctx.drawImage(image, 0, 0, size, size)
  const data = ctx.getImageData(0, 0, size, size).data
  const buckets = new Map<string, Bucket>()

  for (let i = 0; i < data.length; i += 4) {
    const a = data[i + 3]

    if (a < 200) {
      continue
    }

    const r = data[i]
    const g = data[i + 1]
    const b = data[i + 2]
    const { h, l, s } = rgbToHsl({ b, g, r })

    if (l < 0.08 || l > 0.92) {
      continue
    }

    const key = quantizeHsl(h, s, l)
    const weight = 1 + s
    const existing = buckets.get(key)

    if (existing) {
      existing.count += 1
      existing.r += r * weight
      existing.g += g * weight
      existing.b += b * weight
      existing.l += l
      existing.weight += weight
    } else {
      buckets.set(key, { b: b * weight, count: 1, g: g * weight, l, r: r * weight, weight })
    }
  }

  if (buckets.size === 0) {
    return null
  }

  const sorted = [...buckets.values()].sort((a, b) => b.count - a.count)
  const primaryRgb = bucketToRgb(sorted[0])
  const primaryHsl = rgbToHsl(primaryRgb)

  let bestSecondary: { dist: number; rgb: RGB } | null = null
  let bestHighlight: { l: number; rgb: RGB } | null = null

  for (const bucket of buckets.values()) {
    const rgb = bucketToRgb(bucket)
    const hsl = rgbToHsl(rgb)

    const hDist = Math.min(Math.abs(hsl.h - primaryHsl.h), 360 - Math.abs(hsl.h - primaryHsl.h))
    const lDist = Math.abs(hsl.l - primaryHsl.l)
    const dist = hDist / 180 + lDist

    if (hDist >= 25 && lDist >= 0.18 && (!bestSecondary || dist > bestSecondary.dist)) {
      bestSecondary = { dist, rgb }
    }

    if (bucket.l / bucket.weight > primaryHsl.l + 0.04 && (!bestHighlight || hsl.l > bestHighlight.l)) {
      bestHighlight = { l: hsl.l, rgb }
    }
  }

  return {
    highlight: bestHighlight?.rgb ?? primaryRgb,
    primary: primaryRgb,
    secondary: bestSecondary?.rgb ?? primaryRgb
  }
}

function loadImage(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image()

    if (url.startsWith('http://') || url.startsWith('https://')) {
      img.crossOrigin = 'anonymous'
    }

    img.onload = () => resolve(img)
    img.onerror = err => reject(err)
    img.src = url
  })
}

function getThemeBaseAccent(): string {
  // 动态主题共享夜色的底色，强调色混合锚点也用夜色基色，避免切换角色时跳色
  return $theme.get() === 'day' ? '#5c7094' : '#8aa0c8'
}

function applyToCssVariables(skin: PersonaSkin): void {
  if (typeof document === 'undefined') {
    return
  }

  const root = document.documentElement
  root.style.setProperty('--ui-accent', skin.primary)
  root.style.setProperty('--ui-accent-soft', `color-mix(in srgb, ${skin.primary} 18%, transparent)`)
  root.style.setProperty('--ui-accent-line', `color-mix(in srgb, ${skin.primary} 50%, transparent)`)
  root.style.setProperty('--persona-primary', skin.primary)
  root.style.setProperty('--persona-secondary', skin.secondary)
  root.style.setProperty('--persona-highlight', skin.highlight)
  root.style.setProperty(
    '--persona-room-overlay',
    `linear-gradient(180deg, ${skin.secondary}33 0%, rgba(8, 10, 16, 0.55) 100%)`
  )
}

// 清除 persona-skin 在 :root.style 上的强调色覆写，让 styles.css 的 html[data-theme] 块重新生效。
// 仅在切走「动态」主题时调用，避免夜景下强调色仍被角色抽色压住。
function clearPersonaSkinOverrides(): void {
  if (typeof document === 'undefined') {
    return
  }

  const root = document.documentElement
  root.style.removeProperty('--ui-accent')
  root.style.removeProperty('--ui-accent-soft')
  root.style.removeProperty('--ui-accent-line')
  root.style.removeProperty('--persona-primary')
  root.style.removeProperty('--persona-secondary')
  root.style.removeProperty('--persona-highlight')
  root.style.removeProperty('--persona-room-overlay')
  lastAppliedSignature = null
}

let lastAppliedSignature: string | null = null
let transitionResetTimer: ReturnType<typeof setTimeout> | null = null

export async function refreshPersonaSkin(): Promise<void> {
  if ($theme.get() !== 'dynamic') {
    return
  }

  const url = $portraitUrl.get()

  if (!url || (!url.startsWith('http') && !url.startsWith('data:') && !url.startsWith('blob:'))) {
    return
  }

  try {
    const image = await loadImage(url)
    const palette = extractPalette(image)

    if (!palette) {
      return
    }

    // 异步抽色期间用户可能切走「动态」主题：再次校验，避免把角色色覆写回已切走的夜色/日色上
    if ($theme.get() !== 'dynamic') {
      return
    }

    const defaultAccent = getThemeBaseAccent()
    const primaryHsl = sanitizeHsl(rgbToHsl(palette.primary))
    const primaryBlended = blendWithDefault(hslToRgb(primaryHsl), defaultAccent, 0.35)
    const primary = rgbToHex(primaryBlended)

    const secondarySanitized = hslToRgb(sanitizeHsl(rgbToHsl(palette.secondary)))
    const secondaryBlended = blendWithDefault(secondarySanitized, defaultAccent, 0.55)
    const secondary = rgbToHex(secondaryBlended)

    const highlightSanitized = hslToRgb(sanitizeHsl(rgbToHsl(palette.highlight)))
    const highlightBlended = blendWithDefault(highlightSanitized, '#d8c2a8', 0.3)
    const highlight = rgbToHex(highlightBlended)

    const skin: PersonaSkin = { highlight, primary, secondary }
    const signature = `${primary}-${secondary}-${highlight}-${$theme.get()}`

    if (signature === lastAppliedSignature) {
      return
    }

    lastAppliedSignature = signature

    if (typeof document !== 'undefined') {
      const root = document.documentElement
      root.style.setProperty('--persona-skin-transition', `${SKIN_TRANSITION_MS}ms`)

      if (transitionResetTimer !== null) {
        clearTimeout(transitionResetTimer)
      }

      transitionResetTimer = setTimeout(() => {
        root.style.setProperty('--persona-skin-transition', '0ms')
        transitionResetTimer = null
      }, SKIN_TRANSITION_MS + 100)
    }

    applyToCssVariables(skin)
    $personaSkin.set(skin)
  } catch (err) {
    log.warn('persona-skin', 'extract failed', err)
  }
}

export function initPersonaSkin(): () => void {
  const handleThemeChange = (): void => {
    if ($theme.get() === 'dynamic') {
      void refreshPersonaSkin()
    } else {
      clearPersonaSkinOverrides()
    }
  }

  const handlePortraitChange = (): void => {
    if ($theme.get() === 'dynamic') {
      void refreshPersonaSkin()
    }
  }

  const unlistenPortrait = $portraitUrl.listen(handlePortraitChange)
  const unlistenTheme = $theme.listen(handleThemeChange)

  void refreshPersonaSkin()

  return () => {
    unlistenPortrait()
    unlistenTheme()

    if (transitionResetTimer !== null) {
      clearTimeout(transitionResetTimer)
      transitionResetTimer = null
    }
  }
}
