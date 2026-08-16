import * as THREE from 'three'
import { WebGPURenderer } from 'three/webgpu'

import { log } from '@/shared/lib/log'

import { CharacterController } from './CharacterController'
import { reportBackend, reportFrameStats } from './engine-diagnostics'
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

function makeCanvas(container: HTMLElement): HTMLCanvasElement {
  const canvas = document.createElement('canvas')
  canvas.className = 'companion-3d-canvas'
  container.appendChild(canvas)

  return canvas
}

// Same sizing handshake as a React-rendered canvas: read the layout box the
// unstyled 300x150 default canvas occupies inside the shrink-wrapping stage.
function readCanvasSize(canvas: HTMLCanvasElement): { width: number; height: number } {
  return { width: canvas.clientWidth || 320, height: canvas.clientHeight || 320 }
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
    const canvas = makeCanvas(opts.container)
    const size = readCanvasSize(canvas)

    try {
      const gpu = new WebGPURenderer({
        canvas,
        alpha: true,
        antialias: true,
        powerPreference: 'low-power'
      })

      await gpu.init()
      const backend = gpu.backend as { isWebGLBackend?: boolean }
      const kind: EngineBackendKind = backend.isWebGLBackend ? 'webgl2' : 'webgpu'

      log.info('engine', `3D renderer backend: ${kind}`)

      return new Engine(gpu, kind, canvas, size)
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
        antialias: true,
        // 'default' keeps hybrid-GPU laptops on the integrated GPU — the
        // companion scene is far below dGPU territory and forcing it wakes a
        // 20W+ chip for a desk pet.
        powerPreference: 'default'
      })

      return new Engine(classic, 'classic-webgl', fallbackCanvas, fallbackSize)
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
    }
  ) {
    this.renderer = renderer
    this.backendKind = backendKind
    this.canvas = canvas

    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    this.renderer.setSize(size.width, size.height)
    this.renderer.setClearColor(0x000000, 0)
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping
    this.renderer.toneMappingExposure = 1.0
    this.renderer.outputColorSpace = THREE.SRGBColorSpace
    this.renderer.shadowMap.enabled = true
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap

    this.scene = new THREE.Scene()

    // Bust/half-body framing for a desktop companion.
    this.camera = new THREE.PerspectiveCamera(30, size.width / size.height, 0.1, 50)
    this.camera.position.set(0, 1.35, 2.8)
    this.camera.lookAt(0, 1.15, 0)

    this.lighting = new LightingRig(this.scene, this.renderer)

    this.physics = pickBackendFor(backendKind) === 'tsl' ? new TslComputeBackend() : new CpuBackend()
    this.character = new CharacterController(this.physics)

    reportBackend(backendKind)
  }

  frameCharacter(): void {
    if (!this.character.root) {
      return
    }

    this.character.root.updateMatrixWorld(true)
    const box = new THREE.Box3().setFromObject(this.character.root)

    if (box.isEmpty()) {
      return
    }

    const size = box.getSize(new THREE.Vector3())
    const center = box.getCenter(new THREE.Vector3())

    // Frame character so it fills ~95% of the viewport height nicely
    const aspect = this.camera.aspect || 1
    const halfFovRad = THREE.MathUtils.degToRad(this.camera.fov / 2)

    const distH = ((size.y * 0.5) / Math.tan(halfFovRad)) * 1.04
    const distW = ((size.x * 0.5) / (Math.tan(halfFovRad) * aspect)) * 1.04
    const dist = Math.max(distH, distW)

    this.camera.position.set(0, center.y, Math.max(0.5, dist))
    this.camera.lookAt(0, center.y, 0)
  }

  async loadCharacter(bytes: ArrayBuffer | null, rigType: string = 'biped'): Promise<LoadedModelInfo> {
    const info = await this.character.load(bytes, this.scene, rigType)
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
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    this.camera.aspect = width / height
    this.camera.updateProjectionMatrix()
    this.renderer.setSize(width, height)
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
