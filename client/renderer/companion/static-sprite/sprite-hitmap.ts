export interface SpriteHitmap {
  width: number
  height: number
  alpha: Uint8Array
}

export interface SpriteHit {
  el: HTMLImageElement
  hitmap: SpriteHitmap
}

// 256² 把 alpha 缓冲限制在 64 KiB——足够点击定位用。
const HITMAP_MAX_DIM = 256
const HIT_ALPHA_MIN = 32

export function buildSpriteHitmap(img: HTMLImageElement): SpriteHitmap | null {
  const naturalW = img.naturalWidth
  const naturalH = img.naturalHeight

  if (naturalW === 0 || naturalH === 0) {
    return null
  }

  const scale = Math.min(HITMAP_MAX_DIM / naturalW, HITMAP_MAX_DIM / naturalH, 1)
  const width = Math.max(1, Math.round(naturalW * scale))
  const height = Math.max(1, Math.round(naturalH * scale))
  const canvas = document.createElement('canvas')

  canvas.width = width
  canvas.height = height

  const ctx = canvas.getContext('2d')

  if (!ctx) {
    return null
  }

  ctx.drawImage(img, 0, 0, width, height)

  const data = ctx.getImageData(0, 0, width, height).data
  const alpha = new Uint8Array(width * height)

  for (let i = 0; i < alpha.length; i++) {
    alpha[i] = data[i * 4 + 3]
  }

  return { alpha, height, width }
}

export function spriteHitTest(hit: SpriteHit, clientX: number, clientY: number): boolean {
  const rect = hit.el.getBoundingClientRect()

  if (rect.width <= 0 || rect.height <= 0) {
    return false
  }

  // img 用 object-fit: contain 渲染，所以 client 坐标必须经过 letterbox 后的
  // 等比适配矩形映射——直接用元素坐标会把信箱黑边当成精灵本体命中。
  const scale = Math.min(rect.width / hit.hitmap.width, rect.height / hit.hitmap.height)
  const offsetX = rect.left + (rect.width - hit.hitmap.width * scale) / 2
  const offsetY = rect.top + (rect.height - hit.hitmap.height * scale) / 2
  const x = Math.floor((clientX - offsetX) / scale)
  const y = Math.floor((clientY - offsetY) / scale)

  if (x < 0 || x >= hit.hitmap.width || y < 0 || y >= hit.hitmap.height) {
    return false
  }

  return hit.hitmap.alpha[y * hit.hitmap.width + x] >= HIT_ALPHA_MIN
}
