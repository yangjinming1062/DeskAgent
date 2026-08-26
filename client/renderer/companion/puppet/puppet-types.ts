/** Puppet 模块类型 — vendor UMD（window.Rigger / agPsd / GenericParts）的边界契约。 */

interface RigStrand {
  x: number
  tipY: number
  rootY: number
}

export interface RigImage {
  width: number
  height: number
  data: Uint8ClampedArray
}

export interface RigPart {
  name: string
  x: number
  y: number
  w: number
  h: number
  z: number
  depth: number
  group: 'head' | 'body'
  phys: string | null
  fade: string | null
  side: string | null
  strands: RigStrand[] | null
  synthetic?: boolean
  img: RigImage
}

export interface RigEyeAnchor {
  x0: number
  x1: number
  y0: number
  y1: number
  icx: number
  icy: number
  closeY: number
}

export interface RigAnchors {
  face: { cx: number; cy: number; x0: number; x1: number; y0: number; y1: number }
  eyeL?: RigEyeAnchor
  eyeR?: RigEyeAnchor
  mouth: { x0: number; x1: number; y0: number; y1: number; cx: number; cy: number }
  neckPivot: { cx: number; cy: number }
  neckTop: number
  neckBottom: number
  bodyPivot: { cx: number; cy: number }
  faceScale: number
  hairRootY: number
}

export interface Rig {
  canvas: { w: number; h: number }
  layers: RigPart[]
  anchors: RigAnchors
  warnings: string[]
  synth: { eye: boolean; mouth: boolean }
}

interface RiggerLib {
  buildRig(psd: unknown, opts?: { generic?: Record<string, RigImage> }): Rig
  cleanPsdLayers(psd: unknown): { noisy: number; layers: number }
  baseName(name: string): string
  flattenPsdToImg(psd: unknown): RigImage | null
  splitImgLR(img: RigImage): { l: RigImage; r: RigImage } | null
}

interface AgPsdLib {
  readPsd(buf: Uint8Array, opts: { useImageData: boolean; skipThumbnail: boolean }): unknown
  writePsd(psd: unknown, opts: { generateThumbnail: boolean }): Uint8Array
}

interface GenericPartsLib {
  get(key: 'eyeL' | 'eyeR' | 'mouth'): RigImage | undefined
}

declare global {
  interface Window {
    Rigger?: RiggerLib
    agPsd?: AgPsdLib
    GenericParts?: GenericPartsLib
  }
}
