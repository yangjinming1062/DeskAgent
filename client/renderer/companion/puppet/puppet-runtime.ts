/** Puppet WebGL 运行时 — 自 Anime2.5DRig（MIT）index.html 核心移植：
 * 每层 ArtMesh（alpha 轮廓三角剖分）+ deform() 顶点形变（头转/呼吸/眨眼差分/发束弹簧/胸物理）
 * + 模板眼裁切 + 头部三角控制笼重心绑定。形变数学与上游保持一致；GL 装配、rAF 生命周期、
 * 动画自动化层（Phase 1）、网格/绑定（Phase 2）、伪 3D 转头（Phase 3：圆投影深度曲线/
 * 远眼收窄/周边可见度/颈双隶属/上身同源跟随）与次级运动（Phase 4：发束多段弹簧链/裙双频/
 * 耳事件/呆毛/种子化自主段落，机制取自 PuppetLoom）为本仓代码。
 */

import { log } from '@/shared/lib/log'

import { buildArtMesh } from './artmesh'
import { buildHeadCage, cageBary, curveDepth, headBlendMu, type HeadCage } from './head-cage'
import type { Rig, RigAnchors, RigEyeAnchor, RigImage, RigPart } from './puppet-types'
import { ensureVendorLibs } from './vendor-loader'

export interface PuppetParams {
  angleX: number
  angleY: number
  angleZ: number
  eyeOpenL: number
  eyeOpenR: number
  eyeX: number
  eyeY: number
  brow: number
  mouthOpen: number
  mouthForm: number
  mouthCY: number
  body: number
  physAmp: number
  soft: number
  browAngL: number
  browAngR: number
  browAngSym: number
  bangL: number
  bangC: number
  bangR: number
  armY: number
  armPos: number
  bust: number
  bustY: number
  irisScale: number
  mouthEase: number
  eyeEase: number
  fhAmp: number
  fhSoft: number
  eyeCY: number
  eyeCAng: number
  mouthCAng: number
  eyeScaleL: number
  eyeScaleR: number
  mouthScale: number
}

export interface PuppetAuto {
  idle: boolean
  blink: boolean
  rand: boolean
  talk: boolean
  phys: boolean
  gaze: boolean
}

/** 调试/无头验证用：平滑后的活动参数只读快照 */
export interface PuppetSnapshot {
  eyeOpenL: number
  eyeOpenR: number
  eyeX: number
  eyeY: number
  angleX: number
  angleY: number
  angleZ: number
  body: number
  mouthOpen: number
  mouthForm: number
  breath: number
  blinkActive: boolean
}

export function defaultPuppetParams(): PuppetParams {
  return {
    angleX: 0,
    angleY: 0,
    angleZ: 0,
    eyeOpenL: 1,
    eyeOpenR: 1,
    eyeX: 0,
    eyeY: 0,
    brow: 0,
    mouthOpen: 0,
    mouthForm: 0,
    mouthCY: 0,
    body: 0,
    physAmp: 2,
    soft: 2,
    browAngL: 0,
    browAngR: 0,
    browAngSym: 0,
    bangL: 0,
    bangC: 0,
    bangR: 0,
    armY: 0,
    armPos: 0,
    bust: 2.5,
    bustY: 1,
    irisScale: 1,
    mouthEase: 0.45,
    eyeEase: 0.3,
    fhAmp: 2,
    fhSoft: 0.4,
    eyeCY: 0,
    eyeCAng: 0,
    mouthCAng: 0,
    eyeScaleL: 1,
    eyeScaleR: 1,
    mouthScale: 1
  }
}

/** 分参数平滑速率（1/s）：目光快、头慢半拍、眨眼最急 */
const PARAM_RATE: Partial<Record<keyof PuppetParams, number>> = {
  eyeX: 20,
  eyeY: 20,
  eyeOpenL: 22,
  eyeOpenR: 22,
  angleX: 7,
  angleY: 7,
  angleZ: 7,
  body: 5,
  mouthOpen: 16,
  mouthForm: 9
}

/** 吸气快、呼气慢的非对称呼吸曲线（p 为周期相位 [0,1)） */
function breathCurve(p: number): number {
  return p < 0.42 ? smooth(p / 0.42) : 1 - smooth((p - 0.42) / 0.58)
}

/** Phase 3 转头圆投影常量。角度参数 = 归一化正弦（θ = asin(a·sinθmax)）：
 * 中心位移对参数保持线性（与 Phase 2 同幅，可乘 TURN_BOOST 微增），
 * 远/近缘压缩按真实余弦（小角度趋零、满角最强），中立姿态严格保持原图。 */
const TURN_MAX = 0.55
const TURN_SIN = Math.sin(TURN_MAX)
const TURN_COMP = 1 - Math.cos(TURN_MAX)
const TURN_BOOST = 1.15
const COMP_GAIN = 0.85
const PITCH_PROF = 0.085
const FAR_EYE_NARROW = 0.16
const FAR_FADE = 0.55

/** Phase 4 次级运动常量：发束弹簧链节数（PuppetLoom 为 3-5）；裙双频；耳/呆毛事件节奏 */
const HAIR_CHAIN = 4
const SKIRT_W1 = 0.9
const SKIRT_W2 = 2.35

interface GLPart {
  name: string
  bn: string
  side: string | null
  fade: string | null
  group: 'head' | 'body'
  phys: string | null
  depth: number
  x: number
  y: number
  w: number
  h: number
  base: Float32Array
  cur: Float32Array
  nIdx: number
  cb: Float32Array | null
  dEff: Float32Array | null
  mu: Float32Array | null
  invHR: Float32Array | null
  sw: Float32Array | null
  su: Float32Array | null
  bw: Float32Array | null
  spr: { nodes: { x: number; v: number }[]; phase: number }[] | null
  ahoge: { y0: number; y1: number } | null
  tex: WebGLTexture
  vboPos: WebGLBuffer
  vboUV: WebGLBuffer
  ibo: WebGLBuffer
}

type Evaluated = PuppetParams & {
  breath: number
  breathHead: number
  simT: number
  earLift: number
  ahogeDy: number
}

/** 种子化自主段落的动作环：左右观察→抬头→低头，每步之间回正 */
const SEG_CYCLE = ['r', 'n', 'l', 'n', 'u', 'n', 'd', 'n'] as const

function clamp(v: number, a: number, b: number): number {
  return v < a ? a : v > b ? b : v
}

function smooth(t: number): number {
  t = clamp(t, 0, 1)

  return t * t * (3 - 2 * t)
}

export class PuppetRuntime {
  private readonly canvas: HTMLCanvasElement
  private readonly gl: WebGLRenderingContext
  private readonly prog: WebGLProgram
  private readonly locPos: number
  private readonly locUV: number
  private readonly locRes: WebGLUniformLocation | null
  private readonly locCut: WebGLUniformLocation | null
  private readonly locAlpha: WebGLUniformLocation | null

  private layers: GLPart[] = []
  private anchors: RigAnchors | null = null
  private headCage: HeadCage | null = null
  private meshVerts = 0
  private meshTris = 0
  private meshArtmesh = 0
  private meshFallback = 0
  private cw = 768
  private ch = 768
  private fs = 1
  private raf = 0
  private lastNow = 0
  private simPaused = false
  private simNow = 0
  private blinkT = -1
  private blinkFloor = 0
  private nextBlink = performance.now() + 1800
  private gaze: { x: number; y: number } | null = null
  private gazeUntil = 0
  private sac = { x: 0, y: 0 }
  private nextSac = 0
  private segAt = 0
  private segDur = 0
  private segStep = -1
  private segFrom = { ax: 0, ay: 0 }
  private segTo = { ax: 0, ay: 0 }
  private rngState = 0
  private earNext = 0
  private earEvT0 = -1
  private ahogeNext = 0
  private ahogeEvT0 = -1
  private readonly ahogeS = { x: 0, v: 0 }
  private talkOn = false
  private talkV = 0
  private talkTgt = 0
  private talkF = 0
  private talkFTgt = 0
  private talkAmp = 1
  private nextTalkState = 0
  private nextSyl = 0
  private breathP = 0
  private nextSigh = performance.now() + 9000
  private sighUntil = 0
  private lastE: Evaluated | null = null
  private readonly bounce = { x: 0, v: 0, dy: 0 }
  private readonly cur: PuppetParams
  private disposed = false

  /** 外部驱动的目标参数与自动化开关；调用方直接改字段即可。 */
  readonly target: PuppetParams = defaultPuppetParams()
  readonly auto: PuppetAuto = { idle: true, blink: true, rand: true, talk: true, phys: true, gaze: true }
  /** 自主段落/耳/呆毛事件的种子（同种子同时间序列 → 相同动作）；改后调 reseed 生效 */
  autoSeed = 20260826

  onRigApplied: ((rig: Rig) => void) | null = null

  /** 视线焦点注入（归一化 [-1,1]，y 屏幕坐标向下）；传 null 回落到随机漫游。3s 无更新自动过期。 */
  setGaze(x: number | null, y = 0): void {
    this.gaze = x === null ? null : { x: clamp(x, -1.2, 1.2), y: clamp(y, -1, 1) }

    if (this.gaze) {
      this.gazeUntil = (this.simPaused ? this.simNow : performance.now()) + 3000
    }
  }

  /** 立即触发一次眨眼（静息时）；验证与 Phase 6 情绪驱动的确定性钩子。 */
  forceBlink(): void {
    if (this.blinkT < 0) {
      this.blinkT = 0
      this.blinkFloor = 0
    }
  }

  snapshot(): PuppetSnapshot {
    const e = this.lastE

    return {
      eyeOpenL: this.cur.eyeOpenL,
      eyeOpenR: this.cur.eyeOpenR,
      eyeX: this.cur.eyeX,
      eyeY: this.cur.eyeY,
      angleX: this.cur.angleX,
      angleY: this.cur.angleY,
      angleZ: this.cur.angleZ,
      body: this.cur.body,
      mouthOpen: this.cur.mouthOpen,
      mouthForm: this.cur.mouthForm,
      breath: e?.breath ?? 0,
      blinkActive: this.blinkT >= 0
    }
  }

  constructor(canvas: HTMLCanvasElement) {
    this.canvas = canvas
    const gl = canvas.getContext('webgl', { alpha: true, stencil: true, antialias: true, premultipliedAlpha: true })

    if (!gl) {
      throw new Error('WebGL unavailable')
    }

    this.gl = gl

    const sh = (type: number, src: string): WebGLShader => {
      const s = gl.createShader(type)!
      gl.shaderSource(s, src)
      gl.compileShader(s)

      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
        throw new Error(gl.getShaderInfoLog(s) ?? 'shader compile failed')
      }

      return s
    }

    const prog = gl.createProgram()!
    gl.attachShader(
      prog,
      sh(
        gl.VERTEX_SHADER,
        'attribute vec2 aPos; attribute vec2 aUV; uniform vec2 uRes; varying vec2 vUV;' +
          'void main(){ vUV=aUV; vec2 c = aPos/uRes*2.0-1.0; gl_Position=vec4(c.x,-c.y,0.0,1.0); }'
      )
    )
    gl.attachShader(
      prog,
      sh(
        gl.FRAGMENT_SHADER,
        'precision mediump float; varying vec2 vUV; uniform sampler2D uTex; uniform float uCut; uniform float uAlpha;' +
          'void main(){ vec4 c=texture2D(uTex,vUV); if(c.a<uCut) discard; gl_FragColor=c*uAlpha; }'
      )
    )
    gl.linkProgram(prog)
    gl.useProgram(prog)
    this.prog = prog
    this.locPos = gl.getAttribLocation(prog, 'aPos')
    this.locUV = gl.getAttribLocation(prog, 'aUV')
    this.locRes = gl.getUniformLocation(prog, 'uRes')
    this.locCut = gl.getUniformLocation(prog, 'uCut')
    this.locAlpha = gl.getUniformLocation(prog, 'uAlpha')
    gl.enableVertexAttribArray(this.locPos)
    gl.enableVertexAttribArray(this.locUV)
    gl.enable(gl.BLEND)
    gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA)
    gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, true)
    this.cur = defaultPuppetParams()
    this.reseed()
    this.earNext = performance.now() + 6000
    this.ahogeNext = performance.now() + 4000
  }

  /** 重播种子：自主段落与事件调度回到该种子的确定序列。 */
  reseed(): void {
    this.rngState = this.autoSeed | 0
    this.segStep = -1
    this.segAt = 0
    this.segDur = 0
    this.segTo = { ax: 0, ay: 0 }
    this.segFrom = { ax: 0, ay: 0 }
  }

  /** mulberry32 — 事件调度专用的种子化 PRNG（无每帧无关随机数，序列可复现）。 */
  private rng(): number {
    this.rngState = (this.rngState + 0x6d2b79f5) | 0
    let t = this.rngState
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)

    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }

  /** 推进种子化观察段落一步：按环取目标（回正步在两个动作之间），幅度/时长带种子抖动。 */
  private advanceSeg(now: number): void {
    this.segFrom = { ...this.segTo }
    this.segStep = this.segStep < 0 ? 0 : (this.segStep + 1) % SEG_CYCLE.length
    const k = SEG_CYCLE[this.segStep]!
    let dur: number

    if (k === 'n') {
      this.segTo = { ax: 0, ay: 0 }
      dur = 0.9 + this.rng() * 0.6
    } else {
      const j = () => this.rng()

      if (k === 'r') {
        this.segTo = { ax: 0.45 + j() * 0.25, ay: (j() * 2 - 1) * 0.08 }
      } else if (k === 'l') {
        this.segTo = { ax: -(0.45 + j() * 0.25), ay: (j() * 2 - 1) * 0.08 }
      } else if (k === 'u') {
        this.segTo = { ax: (j() * 2 - 1) * 0.12, ay: 0.35 + j() * 0.2 }
      } else {
        this.segTo = { ax: (j() * 2 - 1) * 0.12, ay: -(0.3 + j() * 0.2) }
      }

      dur = 1.2 + this.rng() * 0.4
    }

    this.segAt = now
    this.segDur = dur * 1000
  }

  /** 调试/验证用：指定部件当前顶点相对基准的平均水平位移（px），供次级运动断言。 */
  layerShift(bn: string): number {
    let s = 0
    let n = 0

    for (const L of this.layers) {
      if (L.bn !== bn) {
        continue
      }

      for (let k = 0; k < L.base.length; k += 2) {
        s += Math.abs(L.cur[k]! - L.base[k]!)
        n++
      }
    }

    return n ? s / n : 0
  }

  /** 调试/验证用：发束链梢-根位移差的均值（px），直接反映次级弹簧链输出。 */
  chainSwing(bn: string): number {
    let s = 0
    let n = 0

    for (const L of this.layers) {
      if (L.bn !== bn || !L.spr) {
        continue
      }

      for (const sp of L.spr) {
        const nds = sp.nodes
        s += Math.abs(nds[nds.length - 1]!.x - nds[0]!.x)
        n++
      }
    }

    return n ? s / n : 0
  }

  /** see-through 产出 `-l/-r` 后缀层名，绕过 vendor SLOTS 匹配（side/fade/眼锚点缺失，
   * 虹膜/眉毛/远眼收窄/耳淡出全部失效）——在装配边界补齐眼锚点，side/fade 在 buildGlPart 补。 */
  private patchSideParts(rig: Rig, A: RigAnchors): void {
    const find = (bn: string, side: string): RigPart | undefined =>
      rig.layers.find(l => {
        const n = l.name.toLowerCase()

        return n === `${bn}_${side}` || n === `${bn}-${side}`
      })

    const bbox = (p: RigPart): { x0: number; y0: number; x1: number; y1: number } | null => {
      const { width: w, height: h, data } = p.img
      let x0 = w
      let y0 = h
      let x1 = -1
      let y1 = -1

      for (let y = 0; y < h; y++) {
        for (let x = 0; x < w; x++) {
          if (data[(y * w + x) * 4 + 3]! > 8) {
            if (x < x0) {
              x0 = x
            }

            if (x > x1) {
              x1 = x
            }

            if (y < y0) {
              y0 = y
            }

            if (y > y1) {
              y1 = y
            }
          }
        }
      }

      return x1 < 0 ? null : { x0: p.x + x0, y0: p.y + y0, x1: p.x + x1, y1: p.y + y1 }
    }

    const centroid = (p: RigPart): { x: number; y: number } | null => {
      const { width: w, height: h, data } = p.img
      let sx = 0
      let sy = 0
      let n = 0

      for (let y = 0; y < h; y++) {
        for (let x = 0; x < w; x++) {
          if (data[(y * w + x) * 4 + 3]! > 8) {
            sx += x
            sy += y
            n++
          }
        }
      }

      return n ? { x: p.x + sx / n, y: p.y + sy / n } : null
    }

    for (const side of ['l', 'r'] as const) {
      const key = side === 'l' ? 'eyeL' : 'eyeR'

      if (A[key]) {
        continue
      }

      const ew = find('eyewhite', side)

      if (!ew) {
        continue
      }

      const b = bbox(ew)

      if (!b) {
        continue
      }

      const ic = centroid(find('irides', side) ?? ew)!
      const ecc = find('eye_close', side)
      const cc = ecc ? centroid(ecc) : null // 合成闭眼层质心即睑线近似
      A[key] = {
        x0: b.x0,
        x1: b.x1,
        y0: b.y0,
        y1: b.y1,
        icx: ic.x,
        icy: ic.y,
        closeY: cc ? cc.y : b.y0 + (b.y1 - b.y0) * 0.62
      }
    }
  }

  applyRig(rig: Rig): void {
    const gl = this.gl

    for (const L of this.layers) {
      gl.deleteTexture(L.tex)
      gl.deleteBuffer(L.vboPos)
      gl.deleteBuffer(L.vboUV)
      gl.deleteBuffer(L.ibo)
    }

    this.layers = []
    this.cw = rig.canvas.w
    this.ch = rig.canvas.h
    const A = rig.anchors
    this.anchors = A
    this.fs = A.faceScale
    this.patchSideParts(rig, A)

    // Phase 2: 头部双表面控制笼（dF=脸层深度，dS=头部层最大深度≈头骨）；
    // Phase 3: 头骨横向半径按头部组层外包矩形实测（毛发/耳一般比脸缘宽）
    let dF = 1
    let dS = 2
    let rsMin = Infinity
    let rsMax = -Infinity

    for (const L of rig.layers) {
      if (L.group !== 'head') {
        continue
      }

      dS = Math.max(dS, L.depth)
      rsMin = Math.min(rsMin, L.x)
      rsMax = Math.max(rsMax, L.x + L.w)

      if (window.Rigger?.baseName(L.name.replace(/_(l|r)$/, '')) === 'face') {
        dF = L.depth
      }
    }

    const rs = rsMax > rsMin ? Math.max(Math.abs(rsMin - A.face.cx), Math.abs(rsMax - A.face.cx)) : 0
    this.headCage = buildHeadCage(A, dF, dS, rs)
    this.meshVerts = 0
    this.meshTris = 0
    this.meshArtmesh = 0
    this.meshFallback = 0

    for (const Lr of rig.layers) {
      const L = this.buildGlPart(Lr)
      this.layers.push(L)
    }

    this.canvas.width = this.cw
    this.canvas.height = this.ch
    this.onRigApplied?.(rig)
  }

  private buildGlPart(Lr: RigPart): GLPart {
    const gl = this.gl
    const A = this.anchors!
    const cell = (Lr.phys ? 30 : 42) * Math.max(0.6, this.cw / 768)

    // Phase 2: alpha 轮廓 ArtMesh；退化/空层回退 2×2 quad
    const am = buildArtMesh(Lr.img, cell)
    let nv: number
    let base: Float32Array
    let uv: Float32Array
    let idx: Uint16Array

    if (am) {
      nv = am.verts.length / 2
      base = new Float32Array(nv * 2)
      uv = new Float32Array(nv * 2)

      for (let v = 0; v < nv; v++) {
        const vx = am.verts[v * 2]!
        const vy = am.verts[v * 2 + 1]!
        base[v * 2] = Lr.x + vx
        base[v * 2 + 1] = Lr.y + vy
        uv[v * 2] = vx / Lr.w
        uv[v * 2 + 1] = vy / Lr.h
      }

      idx = am.tris
      this.meshVerts += nv
      this.meshTris += am.stats.tris
      this.meshArtmesh++
    } else {
      nv = 4
      base = new Float32Array([Lr.x, Lr.y, Lr.x + Lr.w, Lr.y, Lr.x, Lr.y + Lr.h, Lr.x + Lr.w, Lr.y + Lr.h])
      uv = new Float32Array([0, 0, 1, 0, 0, 1, 1, 1])
      idx = new Uint16Array([0, 1, 2, 1, 3, 2])
      this.meshVerts += 4
      this.meshTris += 2
      this.meshFallback++
    }

    // see-through 的 `-l/-r` 后缀绕过 vendor SLOTS：规范 bn/side，并给开眼层补 fade
    const bnRaw = Lr.name.replace(/[-_](l|r)$/, '')
    const bn = window.Rigger?.baseName(bnRaw) ?? bnRaw
    const mSide = /[-_](l|r)$/.exec(Lr.name)
    const side = Lr.side ?? (mSide ? mSide[1]!.toUpperCase() : null)
    let fade = Lr.fade

    if (!fade) {
      if (bn === 'eyewhite' || bn === 'irides' || bn === 'eyelash') {
        fade = 'eyeOpen'
      } else if (bn === 'eye_close') {
        fade = 'eyeClose'
      }
    }

    let sw: Float32Array | null = null
    let su: Float32Array | null = null
    let bw: Float32Array | null = null
    let spr: GLPart['spr'] = null
    const S = Lr.strands

    if (S && S.length) {
      const nS = S.length
      let spacing = 120

      if (nS > 1) {
        const ds: number[] = []

        for (let s = 1; s < nS; s++) {
          ds.push(S[s]!.x - S[s - 1]!.x)
        }

        ds.sort((a, b) => a - b)
        spacing = ds[ds.length >> 1] ?? spacing
      }

      const sig = spacing * 0.6
      sw = new Float32Array(nv * nS)
      su = new Float32Array(nv)
      spr = S.map((s, i) => ({
        nodes: Array.from({ length: HAIR_CHAIN }, () => ({ x: 0, v: 0 })),
        phase: i * 1.37 + Lr.z
      }))

      for (let v = 0; v < nv; v++) {
        const x = base[v * 2]!
        const y = base[v * 2 + 1]!
        let tot = 0

        for (let s = 0; s < nS; s++) {
          const w = Math.exp(-(((x - S[s]!.x) / sig) ** 2))
          sw[v * nS + s] = w
          tot += w
        }

        let rY = 0
        let tY = 0

        if (tot > 1e-6) {
          for (let s = 0; s < nS; s++) {
            sw[v * nS + s] = sw[v * nS + s]! / tot
            rY += sw[v * nS + s]! * S[s]!.rootY
            tY += sw[v * nS + s]! * S[s]!.tipY
          }
        } else {
          sw[v * nS] = 1
          rY = S[0]!.rootY
          tY = S[0]!.tipY
        }

        su[v] = clamp((y - rY) / Math.max(1, tY - rY), 0, 1)
      }

      if (bn === 'front hair') {
        const fw = A.face.x1 - A.face.x0
        const fcx = A.face.cx
        const f = 36
        const b1 = fcx - fw * 0.22
        const b2 = fcx + fw * 0.22
        bw = new Float32Array(nv * 3)

        for (let v = 0; v < nv; v++) {
          const x = base[v * 2]!
          const s1 = smooth((x - b1) / f + 0.5)
          const s2 = smooth((x - b2) / f + 0.5)
          bw[v * 3] = 1 - s1
          bw[v * 3 + 1] = s1 * (1 - s2)
          bw[v * 3 + 2] = s2
        }
      }
    }

    // Phase 2: 控制笼绑定 — 重心坐标 + 脸面↔头骨混合 dEff（前发根随脸、梢随颅的连续深度过渡）；
    // Phase 3: 逐顶点 μ 与混合表面横向半径（μ=1 脸面 Rf → μ=0 头骨 Rs）供圆投影用
    const cage = this.headCage
    let cb: Float32Array | null = null
    let dEff: Float32Array | null = null
    let mu: Float32Array | null = null
    let invHR: Float32Array | null = null

    if (cage) {
      cb = new Float32Array(nv * 3)
      dEff = new Float32Array(nv)
      mu = new Float32Array(nv)
      invHR = new Float32Array(nv)
      const muBase = headBlendMu(cage.dF, cage.dS, Lr.depth)
      const dr = cage.rs - cage.rf

      for (let v = 0; v < nv; v++) {
        cageBary(cage, base[v * 2]!, base[v * 2 + 1]!, cb, v * 3)

        let m = muBase

        if (bn === 'front hair' && su) {
          m = Math.min(1, Math.max(0, muBase + 0.15 - 0.3 * su[v]!))
        }

        mu[v] = m
        dEff[v] = cage.dS + m * (cage.dF - cage.dS)
        invHR[v] = 1 / Math.max(1, cage.rf + (1 - m) * dr)
      }
    }

    // 呆毛检测（Phase 4）：前发顶部窄于主发宽 35% 的连续突出段为呆毛，梢部挂纵向弹动
    let ahoge: { y0: number; y1: number } | null = null

    if (bn === 'front hair') {
      const iw = Lr.img.width
      const ih = Lr.img.height
      const dta = Lr.img.data

      const rowW = (y: number): number => {
        let c = 0

        for (let x = 0; x < iw; x++) {
          if (dta[(y * iw + x) * 4 + 3]! > 12) {
            c++
          }
        }

        return c
      }

      const ref = rowW(Math.round(ih * 0.3))
      let top = -1
      let bot = -1

      for (let y = 1; y < ih * 0.3; y += 2) {
        const rw = rowW(y)

        if (rw > 2 && rw < ref * 0.35) {
          if (top < 0) {
            top = y
          }

          bot = y
        } else if (top >= 0) {
          break
        }
      }

      if (top >= 0 && bot - top >= 5) {
        ahoge = { y0: Lr.y + top, y1: Lr.y + bot }
      }
    }

    const vboPos = gl.createBuffer()!
    const vboUV = gl.createBuffer()!
    const ibo = gl.createBuffer()!
    gl.bindBuffer(gl.ARRAY_BUFFER, vboUV)
    gl.bufferData(gl.ARRAY_BUFFER, uv, gl.STATIC_DRAW)
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, ibo)
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, idx, gl.STATIC_DRAW)

    const tex = gl.createTexture()!
    gl.bindTexture(gl.TEXTURE_2D, tex)
    const idata = new ImageData(new Uint8ClampedArray(Lr.img.data), Lr.img.width, Lr.img.height)
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, idata)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)

    return {
      name: Lr.name,
      bn,
      side,
      fade,
      group: Lr.group,
      phys: Lr.phys,
      depth: Lr.depth,
      x: Lr.x,
      y: Lr.y,
      w: Lr.w,
      h: Lr.h,
      base,
      cur: new Float32Array(base),
      nIdx: idx.length,
      cb,
      dEff,
      mu,
      invHR,
      sw,
      su,
      bw,
      spr,
      ahoge,
      tex,
      vboPos,
      vboUV,
      ibo
    }
  }

  start(): void {
    if (this.raf) {
      return
    }

    this.lastNow = performance.now()

    const loop = (now: number): void => {
      if (this.disposed) {
        return
      }

      this.raf = requestAnimationFrame(loop)

      if (this.simPaused) {
        this.render()
      } else {
        this.tickBody(now)
      }
    }

    this.raf = requestAnimationFrame(loop)
  }

  dispose(): void {
    this.disposed = true
    cancelAnimationFrame(this.raf)
    const gl = this.gl

    for (const L of this.layers) {
      gl.deleteTexture(L.tex)
      gl.deleteBuffer(L.vboPos)
      gl.deleteBuffer(L.vboUV)
      gl.deleteBuffer(L.ibo)
    }

    this.layers = []
    gl.deleteProgram(this.prog)
  }

  get size(): { w: number; h: number } {
    return { w: this.cw, h: this.ch }
  }

  /** 网格统计（无头断言用）：ArtMesh 层数 / 回退层数 / 总顶点 / 总三角形。 */
  meshStats(): { layers: number; verts: number; tris: number; artmesh: number; fallback: number } {
    return {
      layers: this.layers.length,
      verts: this.meshVerts,
      tris: this.meshTris,
      artmesh: this.meshArtmesh,
      fallback: this.meshFallback
    }
  }

  private fadeAlpha(L: GLPart, e: Evaluated): number {
    let a = 1

    if (L.fade === 'eyeOpen') {
      const v = L.side === 'L' ? e.eyeOpenL : e.eyeOpenR
      a = smooth((v - (0.1 + e.eyeEase * 0.45)) / 0.15)
    } else if (L.fade === 'eyeClose') {
      const v = L.side === 'L' ? e.eyeOpenL : e.eyeOpenR
      a = 1 - smooth((v - (0.1 + e.eyeEase * 0.45)) / 0.15)
    } else if (L.fade === 'mouthOpen') {
      a = smooth((e.mouthOpen - (0.05 + e.mouthEase * 0.35)) / 0.12)
    } else if (L.fade === 'mouthClose') {
      a = 1 - smooth((e.mouthOpen - (0.05 + e.mouthEase * 0.35)) / 0.12)
    }

    // 周边可见度（Phase 3）：远端侧挂件（耳等；眼/眉只做几何透视，不淡化）随转角淡出
    if (L.side && !L.fade && L.bn !== 'eyebrow') {
      const far = e.angleX * (L.side === 'L' ? 1 : -1)

      if (far > 0) {
        a *= 1 - FAR_FADE * Math.min(1, far)
      }
    }

    return a
  }

  private deform(L: GLPart, e: Evaluated): void {
    const A = this.anchors

    if (!A) {
      return
    }

    const b = L.base
    const o = L.cur
    const n = b.length
    const isHead = L.group === 'head'
    const az = e.angleZ * 0.07
    const cz = Math.cos(az)
    const sz = Math.sin(az)
    const ab = e.body * 0.028
    const cb = Math.cos(ab)
    const sb = Math.sin(ab)
    const NP = A.neckPivot
    const BP = A.bodyPivot
    const FC = { x: A.face.cx, y: A.face.cy }
    const CAGE = this.headCage
    const bn = L.bn
    const eyeSide = L.side
    const EA: RigEyeAnchor | null = eyeSide === 'L' ? (A.eyeL ?? null) : eyeSide === 'R' ? (A.eyeR ?? null) : null
    const vOpen = eyeSide === 'L' ? e.eyeOpenL : e.eyeOpenR
    const mo = e.mouthOpen
    const mHalfW = (A.mouth.x1 - A.mouth.x0) / 2
    const nS = L.spr ? L.spr.length : 0
    const bcx = L.x + L.w / 2
    const bcy = L.y + L.h / 2
    const isFH = bn === 'front hair'
    const isEyePart = bn === 'eyewhite' || bn === 'irides' || bn === 'eyelash' || bn === 'eye_close'
    const farEye = EA ? Math.max(0, e.angleX * (eyeSide === 'L' ? 1 : -1)) : 0

    // Phase 3 转角几何（每层一次）：参数即归一化正弦 → θ = asin(a·sinθmax)；
    // cX = 远/近缘压缩像素系数（满角 = (1-cosθmax)·COMP_GAIN），cY = 俯仰纵向轮廓增益 [0,1]
    const cX = (1 - Math.cos(Math.asin(clamp(e.angleX, -1, 1) * TURN_SIN))) * COMP_GAIN
    const cY = (1 - Math.cos(Math.asin(clamp(e.angleY, -1, 1) * TURN_SIN))) / TURN_COMP

    for (let k = 0; k < n; k += 2) {
      let x = b[k]!
      let y = b[k + 1]!
      const vi = k >> 1

      // 远眼收窄（Phase 3）：转向时对侧眼向眼心水平压缩 — 纯几何透视，不动透明度
      if (farEye > 0 && isEyePart) {
        const cxE = (EA!.x0 + EA!.x1) / 2
        x = cxE + (x - cxE) * (1 - FAR_EYE_NARROW * farEye)
      }

      if (EA && bn === 'eye_close') {
        const sE = eyeSide === 'L' ? e.eyeScaleL : e.eyeScaleR

        if (sE !== 1) {
          const cxE = (EA.x0 + EA.x1) / 2
          const cyE = (EA.y0 + EA.y1) / 2
          x = cxE + (x - cxE) * sE
          y = cyE + (y - cyE) * sE
        }
      }

      if (bn === 'mouth_open' || bn === 'mouth_close') {
        const sM = e.mouthScale

        if (sM !== 1) {
          x = A.mouth.cx + (x - A.mouth.cx) * sM
          y = A.mouth.cy + (y - A.mouth.cy) * sM
        }
      }

      if (L.fade === 'eyeOpen' && EA) {
        if (bn === 'irides') {
          const isc = e.irisScale
          x = EA.icx + (x - EA.icx) * isc
          y = EA.icy + (y - EA.icy) * isc
          x += e.eyeX * 11 * this.fs
          y += e.eyeY * 6 * this.fs
          const tl = smooth((0.32 - vOpen) / 0.32)
          y = EA.closeY + (y - EA.closeY) * (1 - 0.8 * tl)
        } else {
          y = EA.closeY + (y - EA.closeY) * (1 - 0.85 * (1 - vOpen))
        }
      }

      if (L.fade === 'eyeClose' && EA) {
        y -= vOpen * 3
        y += e.eyeCY * 14 * this.fs
        const thE = e.eyeCAng * 0.3 * (eyeSide === 'L' ? 1 : -1)

        if (thE) {
          const ct = Math.cos(thE)
          const st = Math.sin(thE)
          const rx = x - bcx
          const ry = y - bcy
          x = bcx + rx * ct - ry * st
          y = bcy + rx * st + ry * ct
        }
      }

      if (bn === 'eyebrow') {
        y += (-e.brow * 9 + (1 - vOpen) * 3.5) * this.fs
        const th = (eyeSide === 'L' ? e.browAngL + e.browAngSym : e.browAngR - e.browAngSym) * 0.3

        if (th) {
          const ct = Math.cos(th)
          const st = Math.sin(th)
          const rx = x - bcx
          const ry = y - bcy
          x = bcx + rx * ct - ry * st
          y = bcy + rx * st + ry * ct
        }
      }

      if (L.fade === 'mouthOpen') {
        y = A.mouth.y0 + (y - A.mouth.y0) * (0.5 + 0.5 * mo)
        const q = (Math.abs(x - A.mouth.cx) / (mHalfW + 4)) ** 1.5
        y -= e.mouthForm * 6 * this.fs * (q - 0.35)
      }

      if (L.fade === 'mouthClose') {
        y += e.mouthCY * 14 * this.fs
        const thM = e.mouthCAng * 0.35

        if (thM) {
          const ct = Math.cos(thM)
          const st = Math.sin(thM)
          const rx = x - A.mouth.cx
          const ry = y - A.mouth.cy
          x = A.mouth.cx + rx * ct - ry * st
          y = A.mouth.cy + rx * st + ry * ct
        }
      }

      if (bn === 'face' && y > A.mouth.cy) {
        y += mo * 6 * this.fs * smooth((y - A.mouth.cy) / (A.face.y1 - A.mouth.cy))
      }

      let hw = isHead ? 1 : L.group === 'body' ? 0.16 : 0

      // 颈双隶属（Phase 3）：上端完整跟头，下端跟衣领（纵向上相反变化的两组权重）
      if (bn === 'neck') {
        hw = 0.12 + 0.73 * smooth((A.neckBottom - y) / Math.max(1, A.neckBottom - A.neckTop))
      }

      if (hw > 0) {
        const rx = x - NP.cx
        const ry = y - NP.cy
        const rx2 = rx * cz - ry * sz
        const ry2 = rx * sz + ry * cz
        x += (rx2 - rx) * hw
        y += (ry2 - ry) * hw

        // Phase 2 控制点位移 + 重心混合；Phase 3 圆投影深度曲线（仅头/颈走曲线分支，
        // 身体组保持 Phase 2 线性场）：dd 叠加六点脸面曲线（鼻/口等靠前点移动更多，
        // 按 μ 只作用于脸面表面）；横向位移乘可见度轮廓 T=sqrt(1-hx²)——脸缘趋零、
        // 发层因表面半径更大而在同位置保有位移，天然滑过脸缘（侧发贴脸缘）；
        // 压缩项把远/近缘拉向轴心（远缘盖向脸、近缘转回身后）。中立姿态所有新项为零。
        const dd0 = L.dEff ? L.dEff[vi]! : L.depth
        const muV = L.mu ? L.mu[vi]! : 0
        const dd = dd0 + (CAGE ? muV * curveDepth(CAGE, y) : 0)
        const axF = e.angleX * this.fs
        const ayF = e.angleY * this.fs
        const kk = 14 + 40 * (dd - 1)
        const kl = 9 + 30 * (dd - 1)
        const ks = (dd - 1) * 0.05
        let dx: number
        let dy: number

        if (L.cb && CAGE) {
          const w0 = L.cb[vi * 3]!
          const w1 = L.cb[vi * 3 + 1]!
          const w2 = L.cb[vi * 3 + 2]!
          const py0 = CAGE.py[0]!
          const py1 = CAGE.py[1]!
          const py2 = CAGE.py[2]!
          const pb = w0 * (NP.cy - py0) + w1 * (NP.cy - py1) + w2 * (NP.cy - py2)
          const qb = w0 * (py0 - FC.y) + w1 * (py1 - FC.y) + w2 * (py2 - FC.y)

          if (L.invHR && (isHead || bn === 'neck')) {
            const hx = (x - FC.x) * L.invHR[vi]!
            const t = Math.sqrt(Math.max(0, 1 - hx * hx))
            const tp = Math.sqrt(Math.max(0, 1 - ((y - FC.y) / CAGE.rv) ** 2))
            dx = axF * (kk * TURN_BOOST * t + 0.028 * pb) - cX * (x - FC.x)
            dy = -ayF * (kl * tp + (ks + PITCH_PROF * cY) * qb)
          } else {
            dx = axF * (kk + 0.028 * pb)
            dy = -ayF * (kl + ks * qb)
          }
        } else {
          dx = axF * (kk + 0.028 * (NP.cy - y))
          dy = -ayF * (kl + ks * (y - FC.y))
        }

        x += hw * dx
        y += hw * dy
      }

      y -= (L.group === 'body' ? e.breath * 2.0 : e.breathHead * 1.6) * this.fs

      if (bn === 'topwear') {
        const CHEST = this.chest()

        if (y < CHEST.cy) {
          y -= e.breath * 2.2 * this.fs * smooth((CHEST.cy - y) / (CHEST.ry * 2))
        }

        x = NP.cx + (x - NP.cx) * (1 + e.breath * 0.003)
        const gx = (x - CHEST.cx) / CHEST.rx
        const gy = (y - (CHEST.cy + e.bustY * 70 * this.fs)) / CHEST.ry
        y += this.bounce.dy * e.bust * Math.exp(-gx * gx - gy * gy)
      }

      if (bn === 'handwear') {
        const w = smooth(((y - L.y) / L.h) * 1.15)
        y -= e.armY * 30 * this.fs * w
        y += e.armPos * 40 * this.fs
        x += e.armY * 6 * this.fs * w * (x < NP.cx ? 1 : -1)
      }

      // 裙双频（Phase 4）：腰线固定，双频时间线持续左右摆（受待机开关门控，保证姿态定格确定性）
      if (bn === 'bottomwear') {
        const ww = smooth((y - (L.y + L.h * 0.12)) / Math.max(1, L.h * 0.88))
        x += (Math.sin(e.simT * SKIRT_W1 + 1.3) * 2.6 + Math.sin(e.simT * SKIRT_W2 + 4.1) * 1.4) * this.fs * ww
      }

      // 耳事件抬落：根在下（贴头侧）、梢在上，按纵向权重施加深移
      if ((bn === 'ears' || bn === 'earwear') && e.earLift > 0) {
        y -= e.earLift * smooth((L.y + L.h - y) / Math.max(1, L.h))
      }

      // 呆毛纵向弹动：梢（上端）全幅、根（下端）锁定
      if (L.ahoge && e.ahogeDy !== 0) {
        y += e.ahogeDy * smooth((L.ahoge.y1 - y) / Math.max(1, L.ahoge.y1 - L.ahoge.y0)) * 1.4
      }

      if (L.bw && L.su) {
        const m = L.su[vi]! ** 1.4 * 22 * this.fs
        x += (e.bangL * L.bw[vi * 3]! + e.bangC * L.bw[vi * 3 + 1]! + e.bangR * L.bw[vi * 3 + 2]!) * m
      }

      if (nS && this.auto.phys && L.spr && L.sw && L.su) {
        const u = isFH ? Math.min(1, L.su[vi]! * 1.6) : L.su[vi]!
        const amp = u ** (isFH ? 1.8 : 2.1) * (isFH ? e.fhAmp : e.physAmp)
        let dx = 0

        for (let s = 0; s < nS; s++) {
          const w = L.sw[vi * nS + s]!

          if (w < 0.001) {
            continue
          }

          // 发束链按进度取样，相对根节点的偏差即次级运动（根刚随头皮、自由端带惯性）
          const nds = L.spr[s]!.nodes
          const cu = u * (nds.length - 1)
          const i0 = Math.min(nds.length - 2, Math.floor(cu))
          const fr = cu - i0
          dx += w * ((nds[i0]!.x - nds[0]!.x) * (1 - fr) + (nds[i0 + 1]!.x - nds[0]!.x) * fr) * 2.2
        }

        x += dx * amp
        y += Math.abs(dx) * amp * 0.12
      }

      o[k] = x
      o[k + 1] = y
    }

    if (Math.abs(ab) > 1e-4) {
      for (let k = 0; k < n; k += 2) {
        const rx = o[k]! - BP.cx
        const ry = o[k + 1]! - BP.cy
        o[k] = BP.cx + rx * cb - ry * sb
        o[k + 1] = BP.cy + rx * sb + ry * cb
      }
    }
  }

  private chest(): { cx: number; cy: number; rx: number; ry: number } {
    const A = this.anchors!
    const NP = A.neckPivot

    return {
      cx: NP.cx,
      cy: A.neckBottom + (A.face.y1 - A.face.y0) * 0.6,
      rx: (A.face.x1 - A.face.x0) * 0.6,
      ry: (A.face.y1 - A.face.y0) * 0.45
    }
  }

  /** 确定性模拟步进（无头验证/回归基线用）：以固定 1/60 步进接管内部时钟，rAF 退化为纯渲染。
   * Phase 5 十三姿态安全验证与动画回归都以此为准，摆脱 rAF/虚拟时钟的不确定性。 */
  advanceSim(seconds: number): void {
    if (!this.simPaused) {
      this.simPaused = true
      this.simNow = this.lastNow
    }

    let remaining = Math.max(0, seconds)

    while (remaining > 1e-6) {
      const dt = Math.min(1 / 60, remaining)
      this.simNow += dt * 1000
      this.tickBody(this.simNow, { dt, render: false })
      remaining -= dt
    }
  }

  private tickBody(now: number, opts: { dt?: number; render?: boolean } = {}): void {
    const A = this.anchors

    if (!this.layers.length || !A) {
      return
    }

    const dt = opts.dt ?? Math.min(0.05, (now - this.lastNow) / 1000)
    this.lastNow = now
    const t = now / 1000
    const tgt: PuppetParams = { ...this.target }

    if (this.auto.idle) {
      tgt.angleX += 0.13 * Math.sin(t * 0.42) + 0.05 * Math.sin(t * 1.13)
      tgt.angleY += 0.08 * Math.sin(t * 0.31 + 1.7)
      tgt.angleZ += 0.07 * Math.sin(t * 0.23 + 0.5)
      tgt.body += 0.1 * Math.sin(t * 0.19 + 2.1)
    }

    // 视线优先于漫游：有焦点时眼先行（全幅、高速率），头与身体小幅滞后跟随
    const gz = this.auto.gaze && this.gaze && now < this.gazeUntil ? this.gaze : null

    if (gz) {
      tgt.eyeX = clamp(tgt.eyeX * 0.3 + gz.x, -1, 1)
      tgt.eyeY = clamp(tgt.eyeY * 0.3 + gz.y * 0.85, -1, 1)
      tgt.angleX = clamp(tgt.angleX + gz.x * 0.42, -1, 1)
      tgt.angleY = clamp(tgt.angleY - gz.y * 0.26, -1, 1)
      tgt.angleZ = clamp(tgt.angleZ + gz.x * 0.06, -1, 1)
      tgt.body = clamp(tgt.body + gz.x * 0.1, -1, 1)
    } else if (this.auto.rand) {
      // 种子化自主观察段落（Phase 4）：~15s 环内依次左右观察、抬头、低头，动作间回正；
      // 同种子同时间序列 → 相同动作（无每帧随机），眼先于头指向目标
      if (this.segStep < 0 || now >= this.segAt + this.segDur) {
        this.advanceSeg(now)
      }

      const p = smooth(clamp((now - this.segAt) / this.segDur, 0, 1))
      const ax = this.segFrom.ax + (this.segTo.ax - this.segFrom.ax) * p
      const ay = this.segFrom.ay + (this.segTo.ay - this.segFrom.ay) * p
      tgt.angleX = clamp(tgt.angleX + ax, -1, 1)
      tgt.angleY = clamp(tgt.angleY + ay, -1, 1)
      tgt.eyeX = clamp(tgt.eyeX + ax * 0.8, -1, 1)
      tgt.eyeY = clamp(tgt.eyeY - ay * 0.4, -1, 1)
    }

    // 微扫视：注视/漫游之上叠加小幅快速眼动，指数衰减，避免目光发死
    if (this.auto.rand) {
      if (now > this.nextSac) {
        this.nextSac = now + 250 + Math.random() * 1100
        this.sac.x = (Math.random() * 2 - 1) * 0.09
        this.sac.y = (Math.random() * 2 - 1) * 0.05
      }

      const decay = Math.exp(-dt * 1.8)
      this.sac.x *= decay
      this.sac.y *= decay
      tgt.eyeX = clamp(tgt.eyeX + this.sac.x, -1, 1)
      tgt.eyeY = clamp(tgt.eyeY + this.sac.y, -1, 1)
    }

    if (this.auto.talk) {
      if (now > this.nextTalkState) {
        this.talkOn = !this.talkOn
        this.nextTalkState = now + (this.talkOn ? 1200 + Math.random() * 2200 : 600 + Math.random() * 1800)

        if (this.talkOn) {
          this.talkAmp = 0.55 + Math.random() * 0.45
        }
      }

      if (this.talkOn && now > this.nextSyl) {
        this.nextSyl = now + 70 + Math.random() * 110
        this.talkTgt = (Math.random() < 0.25 ? 0.04 : 0.25 + Math.random() * 0.75) * this.talkAmp
        this.talkFTgt = (Math.random() * 2 - 1) * 0.6
      }

      if (!this.talkOn) {
        this.talkTgt = 0
        this.talkFTgt = 0
      }

      this.talkV += (this.talkTgt - this.talkV) * Math.min(1, dt * 22)
      this.talkF += (this.talkFTgt - this.talkF) * Math.min(1, dt * 10)
      tgt.mouthOpen = Math.max(tgt.mouthOpen, this.talkV)
      tgt.mouthForm = clamp(tgt.mouthForm + this.talkF, -1, 1)
    }

    if (this.auto.blink) {
      if (this.blinkT < 0 && now > this.nextBlink) {
        this.blinkT = 0
        this.blinkFloor = Math.random() < 0.2 ? 0.25 + Math.random() * 0.25 : 0
        this.nextBlink = now + 2000 + Math.random() * 5000

        if (Math.random() < 0.16) {
          this.nextBlink = now + 260
        }
      }

      if (this.blinkT >= 0) {
        this.blinkT += dt
        const d = this.blinkT
        const fl = this.blinkFloor
        let v: number

        if (d < 0.08) {
          v = 1 - (1 - fl) * (d / 0.08)
        } else if (d < 0.22) {
          v = fl
        } else if (d < 0.34) {
          v = fl + (1 - fl) * ((d - 0.22) / 0.12)
        } else {
          v = 1
          this.blinkT = -1
        }

        tgt.eyeOpenL = Math.min(tgt.eyeOpenL, v)
        tgt.eyeOpenR = Math.min(tgt.eyeOpenR, v)
      }
    }

    // 上身同源跟随（Phase 3）：直接读平滑后的头部偏航、小比例同刻转向 —
    // 不经过第二套慢响应器，头/颈/肩不会因时间差断开
    tgt.body = clamp(tgt.body + this.cur.angleX * 0.24, -1, 1)

    for (const key of Object.keys(this.cur) as (keyof PuppetParams)[]) {
      this.cur[key] += (tgt[key] - this.cur[key]) * Math.min(1, dt * (PARAM_RATE[key] ?? 14))
    }

    const e: Evaluated = { ...this.cur, breath: 0, breathHead: 0, simT: t, earLift: 0, ahogeDy: 0 }

    // 非对称呼吸（3.4s 周期，吸气快呼气慢）+ 每 18~38s 一次深呼吸；头部相位略滞后
    this.breathP += dt / 3.4

    if (now > this.nextSigh) {
      this.nextSigh = now + 18000 + Math.random() * 20000
      this.sighUntil = now + 3400
    }

    const bAmp = now < this.sighUntil ? 1.5 : 1
    const bp = this.breathP % 1
    e.breath = clamp(breathCurve(bp) * bAmp, 0, 1.5)
    e.breathHead = breathCurve((bp + 0.94) % 1) * bAmp

    // 耳事件（Phase 4，种子化调度）：偶发连续快速抬落约 4 次，随后严格回中立；
    // 与自主段落同门控（auto.rand），姿态定格/验证时保持确定性
    if (this.auto.rand) {
      if (this.earEvT0 < 0 && now > this.earNext) {
        this.earEvT0 = now
        this.earNext = now + 14000 + this.rng() * 16000
      }
    } else {
      this.earEvT0 = -1
    }

    if (this.earEvT0 >= 0) {
      const d = (now - this.earEvT0) / 1000

      if (d > 1.3) {
        this.earEvT0 = -1
      } else {
        e.earLift = Math.abs(Math.sin(d * 9)) * (1 - d / 1.3) * 4.5 * this.fs
      }
    }

    // 呆毛（Phase 4）：平时随发横摆（发束链自动），明显纵向弹动由种子化偶发事件激发；
    // 与耳事件同受 auto.rand 门控，保证姿态定格确定性
    if (this.auto.rand) {
      if (this.ahogeEvT0 < 0 && now > this.ahogeNext) {
        this.ahogeEvT0 = now
        this.ahogeNext = now + 9000 + this.rng() * 12000
        this.ahogeS.v += (this.rng() < 0.5 ? -1 : 1) * 60
      }

      if (this.ahogeEvT0 >= 0 && (now - this.ahogeEvT0) / 1000 > 1.6) {
        this.ahogeEvT0 = -1
      }
    }

    {
      const at = -this.cur.angleY * 10 * this.fs
      const kk = 120
      const cc = 3.5
      const aa = -kk * (this.ahogeS.x - at) - cc * this.ahogeS.v
      this.ahogeS.v += aa * dt
      this.ahogeS.x += this.ahogeS.v * dt
      e.ahogeDy = this.ahogeS.x
    }

    this.lastE = e

    // 发束链驱动（Phase 4）：头/身位移 60/40 混合 + 风动；根节点硬跟随、
    // 下游节点逐节追踪父节点（自由端逐步获得惯性），刚度/阻尼沿链递减
    const headDX = (e.angleX * 14 + e.angleZ * 0.07 * (A.neckPivot.cy - A.face.cy)) * this.fs
    const bodyDX = e.body * 8 * this.fs

    for (const L of this.layers) {
      if (!L.spr) {
        continue
      }

      const isFH = L.bn === 'front hair'
      const softK = 1 + 0.5 * ((isFH ? e.fhSoft : e.soft) / 3)

      for (const sp of L.spr) {
        const wind = this.auto.idle ? 1.8 * Math.sin(t * 0.8 + sp.phase) + 1.0 * Math.sin(t * 1.9 + sp.phase * 2.3) : 0
        const txv = headDX + bodyDX * 0.66 + wind * this.fs
        let prev = txv

        for (let i = 0; i < sp.nodes.length; i++) {
          const nd = sp.nodes[i]!
          const kk = 80 - i * 14
          const cc = (8 - i * 1.6) * softK
          const axv = -kk * (nd.x - prev) - cc * nd.v
          nd.v += axv * dt
          nd.x += nd.v * dt
          prev = nd.x
        }
      }
    }

    {
      const bustTgt = (e.breath * 3.0 - e.angleY * 6.0 + e.body * 4.0) * this.fs
      const kk = 140
      const cc = 4.2
      const aa = -kk * (this.bounce.x - bustTgt) - cc * this.bounce.v
      this.bounce.v += aa * dt
      this.bounce.x += this.bounce.v * dt
      this.bounce.dy = -(this.bounce.x - bustTgt) * 3.0
    }

    if (opts.render !== false) {
      this.render()
    }
  }

  private render(): void {
    const gl = this.gl
    const e = this.lastE

    if (!e || !this.layers.length) {
      return
    }

    gl.viewport(0, 0, this.cw, this.ch)
    gl.clearColor(0, 0, 0, 0)
    gl.clearStencil(0)
    gl.clear(gl.COLOR_BUFFER_BIT | gl.STENCIL_BUFFER_BIT)
    gl.uniform2f(this.locRes, this.cw, this.ch)

    for (const L of this.layers) {
      const fa = this.fadeAlpha(L, e)

      if (fa < 0.004 && !(L.fade === 'eyeOpen' && L.name.indexOf('eyewhite') === 0)) {
        continue
      }

      this.deform(L, e)
      gl.uniform1f(this.locAlpha, fa)
      gl.bindBuffer(gl.ARRAY_BUFFER, L.vboPos)
      gl.bufferData(gl.ARRAY_BUFFER, L.cur, gl.DYNAMIC_DRAW)
      gl.vertexAttribPointer(this.locPos, 2, gl.FLOAT, false, 0, 0)
      gl.bindBuffer(gl.ARRAY_BUFFER, L.vboUV)
      gl.vertexAttribPointer(this.locUV, 2, gl.FLOAT, false, 0, 0)
      gl.bindTexture(gl.TEXTURE_2D, L.tex)
      gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, L.ibo)

      if (L.name.indexOf('eyewhite') === 0) {
        gl.enable(gl.STENCIL_TEST)
        gl.stencilFunc(gl.ALWAYS, 1, 0xff)
        gl.stencilOp(gl.KEEP, gl.KEEP, gl.REPLACE)
        gl.uniform1f(this.locCut, 0.25)
        gl.drawElements(gl.TRIANGLES, L.nIdx, gl.UNSIGNED_SHORT, 0)
        gl.disable(gl.STENCIL_TEST)
        gl.uniform1f(this.locCut, 0.0)
      } else if (L.name.indexOf('irides') === 0) {
        gl.enable(gl.STENCIL_TEST)
        gl.stencilFunc(gl.EQUAL, 1, 0xff)
        gl.stencilOp(gl.KEEP, gl.KEEP, gl.KEEP)
        gl.drawElements(gl.TRIANGLES, L.nIdx, gl.UNSIGNED_SHORT, 0)
        gl.disable(gl.STENCIL_TEST)
      } else {
        gl.drawElements(gl.TRIANGLES, L.nIdx, gl.UNSIGNED_SHORT, 0)
      }
    }
  }
}

/** PSD 字节 → rig 并应用到 runtime；vendor 前置检查与差分合成选项在此收敛。 */
export async function loadPsdIntoRuntime(runtime: PuppetRuntime, psdBuffer: ArrayBuffer): Promise<Rig> {
  await ensureVendorLibs()
  const Rigger = window.Rigger
  const agPsd = window.agPsd

  if (!Rigger || !agPsd) {
    throw new Error('puppet vendor libs not loaded (rigger.js / ag-psd.min.js)')
  }

  const psd = agPsd.readPsd(new Uint8Array(psdBuffer), { useImageData: true, skipThumbnail: true })
  Rigger.cleanPsdLayers(psd)
  const GP = window.GenericParts
  const generic: Record<string, RigImage> = {}

  if (GP) {
    for (const key of ['eyeL', 'eyeR', 'mouth'] as const) {
      const img = GP.get(key)

      if (img) {
        generic[key] = img
      }
    }
  }

  const opts = Object.keys(generic).length ? { generic } : {}
  const rig = Rigger.buildRig(psd, opts)

  if (rig.warnings.length) {
    log.warn('puppet-runtime', 'rig warnings', rig.warnings)
  }

  runtime.applyRig(rig)

  return rig
}
