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
        // MSAA off: per-frame resolve is a meaningful chunk of GPU time at this size; PBR + tonemap already hide jagged silhouettes. WebGPU renderer does not expose `premultipliedAlpha` (the WebGL2 fallback does).
        antialias: false,
        powerPreference: 'low-power'
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
        // MSAA off (same reasoning as WebGPU branch); premultipliedAlpha off skips the multiply during the resolve.
        antialias: false,
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
    this.character = new CharacterController(this.physics)

    reportBackend(backendKind)
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

    // Straight-on face-to-face framing:
    // The camera is placed horizontally level with the character's vertical center.
    // Height fill ratio ~87% ensures ~6.5% breathing room above hair and below feet,
    // completely eliminating top-of-head cropping while filling the 1/3 screen window.
    const aspect = this.camera.aspect || getBaseSpriteWidth() / getBaseSpriteHeight()
    const halfFovRad = THREE.MathUtils.degToRad(this.camera.fov / 2)

    const distH = (height * 0.5) / (Math.tan(halfFovRad) * 0.87)
    const widthSpan = Math.max(Math.min(maxX - minX, height * 0.65), height * 0.42)
    const distW = (widthSpan * 0.5) / (Math.tan(halfFovRad) * aspect * 0.85)
    const dist = Math.max(distH, distW, 0.5)

    // Level straight-on camera (pitch = 0°, eye-level horizontal line of sight)
    this.camera.position.set(centerX, centerY, dist)
    this.camera.lookAt(centerX, centerY, 0)
  }

  async loadCharacter(bytes: ArrayBuffer | null, rigType: string = 'biped', contentHash?: string): Promise<LoadedModelInfo> {
    const info = await this.character.load(bytes, this.scene, rigType, contentHash)
    this.frameCharacter()

    return info
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

      if (now - this.lastFrameAt >= budgetMs - 1) {
        this.lastFrameAt = now
        const delta = Math.min(this.clock.getDelta(), MAX_FRAME_DELTA)
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
    this.renderer.dispose()
    this.canvas.remove()
  }
}
