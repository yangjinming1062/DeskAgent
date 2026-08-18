import * as THREE from 'three'
import { WebGPURenderer } from 'three/webgpu'

import { log } from '@/shared/lib/log'

import { getBaseSpriteHeight, getBaseSpriteWidth } from '../spatial'

import { CharacterController } from './CharacterController'
import { reportBackend, reportEngineError, reportFrameStats } from './engine-diagnostics'
import { LightingRig } from './LightingRig'
import { CpuBackend } from './physics/CpuBackend'
import { type PhysicsBackend, pickBackendFor } from './physics/PhysicsBackend'
import { TslComputeBackend } from './physics/TslComputeBackend'
import type { PowerProfile } from './PowerProfile'
import { PROFILE_FPS } from './PowerProfile'
import type { RenderStyle } from './style/types'
import type { EngineBackendKind, EngineOptions, LoadedModelInfo } from './types'

type AnyRenderer = THREE.WebGLRenderer | WebGPURenderer

// dormant is timer-driven at 4fps rather than rAF-gated: lock screens and
// occluded windows stop rAF entirely, while the process ships with Chromium
// timer throttling disabled, so setTimeout keeps a steady cadence.
const DORMANT_TICK_MS = 250
// Hidden windows hard-stop rAF even with the anti-throttling switches; an
// active-profile companion under an occluder (e.g. speaking) keeps animating
// from a timer until visibility returns.
const HIDDEN_ACTIVE_MS = 16
const HIDDEN_IDLE_MS = 37
// Wake clamp — a dormant→active transition must not feed the mixer or the
// verlet solver a multi-second delta (ClothSolver clamps its own, the mixer
// does not).
const MAX_FRAME_DELTA = 0.05

// DPR cap. 1.5 is enough for a 300×360 desktop-pet window — going higher
// (e.g. 2.0) doubles the shader work without any visible quality gain at
// the sprite's native display size. iGPU + alpha:true is fillrate-bound.
const MAX_DPR = 1.5

// Silhouette hitmap: 1/4 canvas res bounds the extra render + readback; the
// TTL caps refreshes at 4 Hz while the cursor sweeps the sprite rect.
const HITMAP_SCALE = 4
const HITMAP_TTL_MS = 250

export interface SilhouetteHitmap {
  alpha: Uint8Array
  width: number
  height: number
}

function makeCanvas(container: HTMLElement): HTMLCanvasElement {
  const canvas = document.createElement('canvas')
  canvas.className = 'companion-3d-canvas'
  canvas.style.width = '100%'
  canvas.style.height = '100%'
  canvas.style.display = 'block'
  container.appendChild(canvas)

  return canvas
}

// Read the layout box the canvas occupies inside the companion container.
function readCanvasSize(canvas: HTMLCanvasElement): { width: number; height: number } {
  const parent = canvas.parentElement
  const width = parent?.clientWidth || canvas.clientWidth || getBaseSpriteWidth()
  const height = parent?.clientHeight || canvas.clientHeight || getBaseSpriteHeight()

  return { width, height }
}

export class Engine {
  readonly renderer: AnyRenderer
  readonly backendKind: EngineBackendKind
  readonly canvas: HTMLCanvasElement
  readonly scene: THREE.Scene
  readonly camera: THREE.PerspectiveCamera
  readonly clock = new THREE.Clock()
  readonly character: CharacterController
  readonly lighting: LightingRig
  private readonly physics: PhysicsBackend
  /** Measured render rate, refreshed once per second (diagnostics only). */
  readonly stats = { fps: 0 }

  private rafId: number | null = null
  private timerId: ReturnType<typeof setTimeout> | null = null
  private disposed = false
  private running = false
  private isTicking = false
  private profile: PowerProfile = 'active'
  private lastFrameAt = 0
  private statsFrames = 0
  private statsWindowStart = 0
  private hitRT: THREE.RenderTarget | null = null
  private hitMap: SilhouetteHitmap | null = null
  private hitMapAt = 0
  private hitRefresh: Promise<SilhouetteHitmap | null> | null = null

  // Async factory: WebGPURenderer.init() owns the first two tiers of the
  // fallback chain (WebGPU backend → its built-in WebGL2 retry). Only a full
  // init rejection drops to the classic WebGLRenderer — on a fresh canvas,
  // because a canvas that ever hosted a webgpu context never yields a webgl2
  // one. Anything beyond that propagates to the caller (static-sprite mode
  // is the never-blank floor).
  static async create(opts: EngineOptions): Promise<Engine> {
    const useShadows = opts.useShadows ?? false
    const canvas = makeCanvas(opts.container)
    const size = readCanvasSize(canvas)

    try {
      const gpu = new WebGPURenderer({
        canvas,
        alpha: true,
        // MSAA 4×: the companion floats over arbitrary desktop content, so
        // silhouette crawl is the top visual defect — and at ≤450×540 px the
        // per-frame resolve is negligible even on iGPU. WebGPU renderer does
        // not expose `premultipliedAlpha` (the WebGL2 fallback does).
        antialias: true
      })

      await gpu.init()
      const backend = gpu.backend as { isWebGLBackend?: boolean }
      const kind: EngineBackendKind = backend.isWebGLBackend ? 'webgl2' : 'webgpu'

      log.info('engine', `3D renderer backend: ${kind}`)

      return new Engine(gpu, kind, canvas, size, useShadows)
    } catch (err) {
      log.warn('engine', 'WebGPURenderer init failed, falling back to classic WebGLRenderer:', err)
      canvas.remove()
    }

    const fallbackCanvas = makeCanvas(opts.container)
    const fallbackSize = readCanvasSize(fallbackCanvas)

    try {
      const classic = new THREE.WebGLRenderer({
        canvas: fallbackCanvas,
        alpha: true,
        // MSAA on (same reasoning as WebGPU branch); premultipliedAlpha off skips the multiply during the resolve.
        antialias: true,
        premultipliedAlpha: false,
        // 'default' keeps hybrid-GPU laptops on the integrated GPU — the companion scene is far below dGPU territory and forcing it wakes a 20W+ chip for a desk pet.
        powerPreference: 'default'
      })

      return new Engine(classic, 'classic-webgl', fallbackCanvas, fallbackSize, useShadows)
    } catch (err) {
      // No GPU context at all — release the orphan canvas before propagating
      // (static-sprite mode is the never-blank floor).
      fallbackCanvas.remove()
      throw err
    }
  }

  private constructor(
    renderer: AnyRenderer,
    backendKind: EngineBackendKind,
    canvas: HTMLCanvasElement,
    size: {
      width: number
      height: number
    },
    useShadows: boolean
  ) {
    this.renderer = renderer
    this.backendKind = backendKind
    this.canvas = canvas

    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, MAX_DPR))
    this.renderer.setSize(size.width, size.height, false)
    this.renderer.setClearColor(0x000000, 0)
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping
    this.renderer.toneMappingExposure = 1.0
    this.renderer.outputColorSpace = THREE.SRGBColorSpace
    this.renderer.shadowMap.enabled = useShadows
    // 1024² PCF (no Soft) when shadows are on — half the bandwidth of the old 2048² PCFSoft default.
    this.renderer.shadowMap.type = useShadows ? THREE.PCFShadowMap : THREE.PCFSoftShadowMap

    this.scene = new THREE.Scene()

    // Straight-on (face-to-face) telephoto portrait camera setup — 14° FOV provides true orthographic-like parallel perspective, eliminating chin/feet keystoning
    this.camera = new THREE.PerspectiveCamera(14, size.width / size.height, 0.1, 50)
    this.camera.position.set(0, 0.9, 6.0)
    this.camera.lookAt(0, 0.9, 0)

    this.lighting = new LightingRig(this.scene, this.renderer, useShadows)

    this.physics = pickBackendFor(backendKind) === 'tsl' ? new TslComputeBackend() : new CpuBackend()
    this.character = new CharacterController(this.physics, { nodePipeline: backendKind !== 'classic-webgl' })

    reportBackend(backendKind)
  }

  /** Current-frame silhouette alpha at ~1/4 canvas resolution. Renders the
   * scene into an offscreen target — clear alpha is 0, so only drawn pixels
   * count and the map tracks the live pose, cloth and outline hulls exactly.
   * Cached HITMAP_TTL_MS; concurrent callers share one refresh. Null only
   * when the readback itself fails. */
  async silhouetteHitmap(): Promise<SilhouetteHitmap | null> {
    if (this.hitMap && performance.now() - this.hitMapAt < HITMAP_TTL_MS) {
      return this.hitMap
    }

    this.hitRefresh ??= this.refreshHitmap().finally(() => {
      this.hitRefresh = null
    })

    return this.hitRefresh
  }

  getSilhouetteHitmap(): SilhouetteHitmap | null {
    return this.hitMap
  }

  private async refreshHitmap(): Promise<SilhouetteHitmap | null> {
    const canvasW = this.canvas.clientWidth || this.canvas.parentElement?.clientWidth || getBaseSpriteWidth()
    const canvasH = this.canvas.clientHeight || this.canvas.parentElement?.clientHeight || getBaseSpriteHeight()
    const w = Math.max(1, Math.round(canvasW / HITMAP_SCALE))
    const h = Math.max(1, Math.round(canvasH / HITMAP_SCALE))

    if (!this.hitRT || this.hitRT.width !== w || this.hitRT.height !== h) {
      this.hitRT?.dispose()
      // The classic tier's async read demands a WebGLRenderTarget; the node
      // tiers take the core RenderTarget.
      this.hitRT =
        this.backendKind === 'classic-webgl' ? new THREE.WebGLRenderTarget(w, h) : new THREE.RenderTarget(w, h)
    }

    const rt = this.hitRT

    try {
      // Param cast narrows the renderer union — the node tier accepts the
      // RenderTarget supertype, the classic tier the WebGLRenderTarget both
      // tiers construct here per kind.
      try {
        this.renderer.setRenderTarget(rt as THREE.WebGLRenderTarget)
        this.renderer.render(this.scene, this.camera)
      } finally {
        this.renderer.setRenderTarget(null)
      }

      const data =
        this.backendKind === 'classic-webgl'
          ? await this.readClassicPixels(rt as THREE.WebGLRenderTarget, w, h)
          : await (this.renderer as WebGPURenderer).readRenderTargetPixelsAsync(rt, 0, 0, w, h)

      if (this.disposed) {
        return null
      }

      const alpha = new Uint8Array(w * h)
      // WebGPU copyTextureToBuffer aligns each row to 256 bytes; WebGL readPixels is tightly packed.
      // Normalize to top-down row order matching DOM client space (y=0 at top of canvas).
      const isWebGPU = this.backendKind === 'webgpu'
      const isBottomUp = this.backendKind !== 'webgpu'
      const rowStrideBytes = isWebGPU ? Math.ceil((w * 4) / 256) * 256 : w * 4

      for (let y = 0; y < h; y++) {
        const srcY = isBottomUp ? h - 1 - y : y
        const srcRowOffset = srcY * rowStrideBytes
        const dstRowOffset = y * w

        for (let x = 0; x < w; x++) {
          alpha[dstRowOffset + x] = data[srcRowOffset + x * 4 + 3]
        }
      }

      this.hitMap = { alpha, height: h, width: w }
      this.hitMapAt = performance.now()

      return this.hitMap
    } catch (err) {
      log.warn('engine', 'silhouette hitmap readback failed:', err)

      return null
    }
  }

  private async readClassicPixels(rt: THREE.WebGLRenderTarget, w: number, h: number): Promise<Uint8Array> {
    const buf = new Uint8Array(w * h * 4)
    await (this.renderer as THREE.WebGLRenderer).readRenderTargetPixelsAsync(rt, 0, 0, w, h, buf)

    return buf
  }

  frameCharacter(): void {
    if (!this.character.root) {
      return
    }

    this.character.root.updateMatrixWorld(true)

    // Check if we have skeleton bones to calculate real posed/skinned world positions
    const bones: THREE.Bone[] = []
    this.character.root.traverse(child => {
      if (child instanceof THREE.Bone) {
        bones.push(child)
      }
    })

    const headBone = bones.find(b => {
      const name = b.name.toLowerCase()

      return b.name === 'Head' || b.name === 'mixamorigHead' || name.endsWith('head')
    })

    let minY = Infinity
    let maxY = -Infinity
    let minX = Infinity
    let maxX = -Infinity

    if (bones.length >= 3) {
      const v = new THREE.Vector3()

      for (const bone of bones) {
        bone.getWorldPosition(v)

        if (v.y < minY) {
          minY = v.y
        }

        if (v.y > maxY) {
          maxY = v.y
        }

        if (v.x < minX) {
          minX = v.x
        }

        if (v.x > maxX) {
          maxX = v.x
        }
      }

      // Add generous head top clearance (~26cm above Head bone for skull, hair buns, and volume)
      // and foot sole clearance (~6cm below Foot/Toe bone for shoes and ground plane)
      maxY += 0.26
      minY -= 0.06
    } else {
      // Fallback: Box3 from meshes
      const box = new THREE.Box3().setFromObject(this.character.root)

      if (box.isEmpty()) {
        return
      }

      minY = box.min.y
      maxY = box.max.y
      minX = box.min.x
      maxX = box.max.x
    }

    const height = Math.max(0.1, maxY - minY)
    const centerY = (minY + maxY) / 2
    const centerX = (minX + maxX) / 2

    // Straight-on eye-level (平视) framing:
    // Determine eye-level optical axis height (~72% of character height, or Head bone level for bipeds).
    // Placing the camera at eye level with 0° pitch guarantees a true straight-on perspective without chin-up distortion.
    let targetY = centerY

    if (this.character.isBipedRig || bones.length >= 3) {
      if (headBone) {
        const headPos = new THREE.Vector3()
        headBone.getWorldPosition(headPos)
        targetY = THREE.MathUtils.clamp(headPos.y - 0.05, centerY, maxY - 0.1)
      } else {
        targetY = minY + height * 0.72
      }
    }

    // Height fill ratio ~87% ensures ~6.5% breathing room above hair and below feet,
    // completely eliminating top-of-head cropping while filling the 1/3 screen window.
    const aspect = this.camera.aspect || getBaseSpriteWidth() / getBaseSpriteHeight()
    const halfFovRad = THREE.MathUtils.degToRad(this.camera.fov / 2)

    const distH = (height * 0.5) / (Math.tan(halfFovRad) * 0.87)
    const widthSpan = Math.max(Math.min(maxX - minX, height * 0.65), height * 0.42)
    const distW = (widthSpan * 0.5) / (Math.tan(halfFovRad) * aspect * 0.85)
    const dist = Math.max(distH, distW, 0.5)

    // Level straight-on camera (pitch = 0°, eye-level horizontal line of sight)
    this.camera.position.set(centerX, targetY, dist)
    this.camera.lookAt(centerX, targetY, 0)

    // Shift projection window vertically via setViewOffset so the full body remains centered
    // from head to toe within the canvas window without tilting the camera optical axis.
    const canvasWidth = this.canvas.clientWidth || getBaseSpriteWidth()
    const canvasHeight = this.canvas.clientHeight || getBaseSpriteHeight()
    const visibleWorldHeight = ((height * 0.5) / 0.87) * 2
    const deltaY = targetY - centerY
    const yOffset = (deltaY / visibleWorldHeight) * canvasHeight

    if (Math.abs(yOffset) > 0.5) {
      this.camera.setViewOffset(canvasWidth, canvasHeight, 0, yOffset, canvasWidth, canvasHeight)
    } else {
      this.camera.clearViewOffset()
    }
  }

  async loadCharacter(
    bytes: ArrayBuffer | null,
    rigType: string = 'biped',
    contentHash?: string
  ): Promise<LoadedModelInfo> {
    const info = await this.character.load(bytes, this.scene, rigType, contentHash)
    this.frameCharacter()
    this.hitMap = null
    this.hitMapAt = 0

    return info
  }

  /** Hot-switch NPR toon ⇄ PBR: materials via the character controller,
   * lighting preset via the rig. */
  setRenderStyle(style: RenderStyle): void {
    this.character.setRenderStyle(style)
    this.lighting.setStyleProfile(style)
  }

  start(): void {
    if (this.running || this.disposed) {
      return
    }

    this.running = true
    this.clock.getDelta()
    document.addEventListener('visibilitychange', this.onVisibilityChange)
    this.scheduleNext()
  }

  stop(): void {
    this.running = false
    document.removeEventListener('visibilitychange', this.onVisibilityChange)
    this.cancelPendingLoop()
  }

  setPowerProfile(profile: PowerProfile): void {
    if (profile === this.profile) {
      return
    }

    this.profile = profile

    // Re-schedule under the new cadence; cancelPendingLoop closes the race
    // where the previous rAF/timer callback is still queued.
    if (this.running && !this.disposed) {
      this.cancelPendingLoop()
      this.scheduleNext()
    }
  }

  private onVisibilityChange = (): void => {
    // A hidden window never fires the pending rAF — swap it for the timer
    // fallback without waiting for a profile change.
    if (this.running && !this.disposed) {
      this.cancelPendingLoop()
      this.scheduleNext()
    }
  }

  private tick = (): void => {
    this.rafId = null
    this.timerId = null

    if (!this.running || this.disposed || this.isTicking) {
      return
    }

    this.isTicking = true
    const now = performance.now()

    try {
      const budgetMs = 1000 / PROFILE_FPS[this.profile]

      // Allow a 25% tolerance on the frame budget (e.g. >= 12.5ms for 60fps) so 60Hz displays
      // never drop a frame due to sub-millisecond rAF timer jitter, and 120Hz displays render
      // cleanly on alternate VSync ticks.
      if (now - this.lastFrameAt >= budgetMs * 0.75) {
        const elapsed = this.lastFrameAt > 0 ? (now - this.lastFrameAt) / 1000 : 1 / 60
        this.lastFrameAt = now
        const delta = Math.min(Math.max(0.001, elapsed), MAX_FRAME_DELTA)
        this.physics.beginFrame()
        this.character.update(delta)

        // One dispatch per node keeps pass ordering explicit (skin →
        // constraints → collide → normals); the CPU backend yields none.
        if (this.renderer instanceof WebGPURenderer) {
          for (const node of this.physics.collectCompute()) {
            this.renderer.compute(node)
          }
        }

        this.renderer.render(this.scene, this.camera)

        if (this.statsWindowStart === 0) {
          this.statsWindowStart = now
        }

        this.statsFrames++

        if (now - this.statsWindowStart >= 1000) {
          this.stats.fps = (this.statsFrames * 1000) / (now - this.statsWindowStart)
          this.statsFrames = 0
          this.statsWindowStart = now
          reportFrameStats(this.profile, this.stats.fps)
        }
      }
    } catch (err) {
      // Render guard — stop the ticker on first error so the next frame doesn't repeat the same throw; surface via $engineError for the dev overlay.
      this.running = false
      const message = err instanceof Error ? err.message : String(err)
      reportEngineError(message)
      log.error('engine', 'ticker stopped after error:', err)

      return
    } finally {
      this.isTicking = false
    }

    this.scheduleNext()
  }

  private scheduleNext(): void {
    if (this.disposed) {
      return
    }

    if (this.profile === 'dormant') {
      this.timerId = setTimeout(this.tick, DORMANT_TICK_MS)
    } else if (document.visibilityState === 'hidden') {
      this.timerId = setTimeout(this.tick, this.profile === 'active' ? HIDDEN_ACTIVE_MS : HIDDEN_IDLE_MS)
    } else {
      this.rafId = requestAnimationFrame(this.tick)
    }
  }

  private cancelPendingLoop(): void {
    if (this.rafId !== null) {
      cancelAnimationFrame(this.rafId)
      this.rafId = null
    }

    if (this.timerId !== null) {
      clearTimeout(this.timerId)
      this.timerId = null
    }
  }

  resize(width: number, height: number): void {
    // Re-pick pixel ratio: window.devicePixelRatio changes when the window
    // is dragged across monitors with different DPI.
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, MAX_DPR))
    this.camera.aspect = width / height
    this.camera.updateProjectionMatrix()
    this.renderer.setSize(width, height, false)
    this.frameCharacter()
  }

  dispose(): void {
    this.disposed = true
    this.stop()
    this.lighting.dispose(this.scene)
    this.physics.dispose()
    this.character.dispose()
    this.hitRT?.dispose()
    this.hitRT = null
    this.hitMap = null
    this.renderer.dispose()
    this.canvas.remove()
  }
}
