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

// dormant 档位由定时器以 4fps 驱动，而非 rAF：
// 锁屏与被遮挡窗口会完全停止 rAF，而本进程关闭了 Chromium 定时器限流，
// 因此 setTimeout 仍能保持稳定节奏。
const DORMANT_TICK_MS = 250
// 即使加了反限流开关，隐藏窗口仍会硬性停止 rAF。
// 处于活跃档位且被遮挡的伙伴（例如正在说话）会改用定时器继续推进动画，
// 直到窗口恢复可见。
const HIDDEN_ACTIVE_MS = 16
const HIDDEN_IDLE_MS = 37
// 唤醒钳位——dormant→active 切换时不能把多秒级的 delta 喂给
// mixer 或 verlet 求解器（ClothSolver 自行钳位，mixer 不会）。
const MAX_FRAME_DELTA = 0.05

// DPR 上限。1.5 对 300×360 的桌面伙伴窗口已经足够——
// 再高（如 2.0）会把 shader 工作量翻倍，
// 但在精灵原生显示尺寸下肉眼无差别。iGPU + alpha:true 受 fillrate 限制。
const MAX_DPR = 1.5

// 剪影命中图：1/4 canvas 分辨率上限限定额外渲染与回读开销；
// TTL 在光标扫过精灵矩形时将刷新频率限制为 4 Hz。
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

// 读取 canvas 在伙伴容器内的布局盒子。
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

  // 异步工厂：WebGPURenderer.init() 负责回退链的前两档
  // （WebGPU 后端 → 自带的 WebGL2 重试）。只有 init 完全失败
  // 才会降级到经典 WebGLRenderer——前提是新 canvas，
  // 因为一旦承载过 webgpu 上下文的 canvas 不会再产出 webgl2 上下文。
  // 进一步失败则向上抛给调用方（静态精灵层就是"永不空白"的底线）。
  static async create(opts: EngineOptions): Promise<Engine> {
    const useShadows = opts.useShadows ?? false
    const canvas = makeCanvas(opts.container)
    const size = readCanvasSize(canvas)

    try {
      const gpu = new WebGPURenderer({
        canvas,
        alpha: true,
        // MSAA 4×：伙伴窗口悬浮在任意桌面内容之上，
        // 走样的剪影是最显眼的瑕疵——而在 ≤450×540 px 范围内，
        // 即使在 iGPU 上每帧 resolve 的开销也可忽略。
        // WebGPU 渲染器未暴露 `premultipliedAlpha`（WebGL2 回退分支会暴露）。
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
        // 启用 MSAA（理由同上）；关闭 premultipliedAlpha 可以在 resolve 时省掉一次乘法。
        antialias: true,
        premultipliedAlpha: false,
        // 'default' keeps hybrid-GPU laptops on the integrated GPU — the companion scene is far below dGPU territory and forcing it wakes a 20W+ chip for a desk pet.
        powerPreference: 'default'
      })

      return new Engine(classic, 'classic-webgl', fallbackCanvas, fallbackSize, useShadows)
    } catch (err) {
      // 完全拿不到 GPU 上下文——在向上抛错前释放这个孤儿 canvas
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

    // 正面对视（face-to-face）的长焦人像相机设置——14° FOV 接近真正的正交平行透视，避免下巴/脚部梯形失真
    this.camera = new THREE.PerspectiveCamera(14, size.width / size.height, 0.1, 50)
    this.camera.position.set(0, 0.9, 6.0)
    this.camera.lookAt(0, 0.9, 0)

    this.lighting = new LightingRig(this.scene, this.renderer, useShadows)

    this.physics = pickBackendFor(backendKind) === 'tsl' ? new TslComputeBackend() : new CpuBackend()
    this.character = new CharacterController(this.physics)

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

  private async refreshHitmap(): Promise<SilhouetteHitmap | null> {
    const canvasW = this.canvas.clientWidth || this.canvas.parentElement?.clientWidth || getBaseSpriteWidth()
    const canvasH = this.canvas.clientHeight || this.canvas.parentElement?.clientHeight || getBaseSpriteHeight()
    const w = Math.max(1, Math.round(canvasW / HITMAP_SCALE))
    const h = Math.max(1, Math.round(canvasH / HITMAP_SCALE))

    if (!this.hitRT || this.hitRT.width !== w || this.hitRT.height !== h) {
      this.hitRT?.dispose()
      // 经典档位的异步 read 需要 WebGLRenderTarget；节点档位接受核心 RenderTarget。
      this.hitRT =
        this.backendKind === 'classic-webgl' ? new THREE.WebGLRenderTarget(w, h) : new THREE.RenderTarget(w, h)
    }

    const rt = this.hitRT

    try {
      // 参数转换收窄渲染器联合类型——节点档位接受 RenderTarget 父类型，
      // 经典档位接受 WebGLRenderTarget，两档位在此按 kind 各自构造。
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
      // WebGPU 的 copyTextureToBuffer 每行按 256 字节对齐；WebGL 的 readPixels 紧凑排列。
      // 统一为自上而下的行序，与 DOM client 空间一致（y=0 在 canvas 顶部）。
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

    // 检查是否有骨骼以计算真实的蒙皮后世界坐标
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

      // 头部预留较大顶部空间（Head 骨骼上方约 26cm，用于头骨、发髻与体积）
      // 以及脚底空间（Foot/Toe 骨骼下方约 6cm，用于鞋底与地面）
      maxY += 0.26
      minY -= 0.06
    } else {
      // 兜底：由网格构造 Box3
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

    // 平视取景：
    // 确定视线轴的高度（约为角色身高的 72%，或双足生物的 Head 骨骼高度）。
    // 相机放在平视高度且俯仰角 0°，可保证真正的正面对视透视，避免仰视失真。
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

    // 高度占比约 87%——头顶与脚底各预留约 6.5% 的呼吸空间，
    // 在填满 1/3 屏窗口的同时，彻底避免头顶裁切。
    const aspect = this.camera.aspect || getBaseSpriteWidth() / getBaseSpriteHeight()
    const halfFovRad = THREE.MathUtils.degToRad(this.camera.fov / 2)

    const distH = (height * 0.5) / (Math.tan(halfFovRad) * 0.87)
    const widthSpan = Math.max(Math.min(maxX - minX, height * 0.65), height * 0.42)
    const distW = (widthSpan * 0.5) / (Math.tan(halfFovRad) * aspect * 0.85)
    const dist = Math.max(distH, distW, 0.5)

    // 水平的正面对视相机（俯仰角 0°，平视水平视线）
    this.camera.position.set(centerX, targetY, dist)
    this.camera.lookAt(centerX, targetY, 0)

    // 通过 setViewOffset 垂直偏移投影窗口，使全身从头到脚
    // 在 canvas 窗口内居中显示，而无需倾斜相机光轴。
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

    // 按新节奏重新调度；cancelPendingLoop 关闭旧 rAF/定时器回调仍排队时的竞态。
    if (this.running && !this.disposed) {
      this.cancelPendingLoop()
      this.scheduleNext()
    }
  }

  private onVisibilityChange = (): void => {
    // 隐藏窗口永远不会触发排队的 rAF——直接换成定时器回退，
    // 无需等待档位变更。
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

      // 帧预算允许 25% 的容差（如 60fps 下 ≥12.5ms），
      // 让 60Hz 显示器不会因亚毫秒级的 rAF 抖动丢帧，
      // 让 120Hz 显示器能在隔一拍的 VSync 上干净渲染。
      if (now - this.lastFrameAt >= budgetMs * 0.75) {
        const elapsed = this.lastFrameAt > 0 ? (now - this.lastFrameAt) / 1000 : 1 / 60
        this.lastFrameAt = now
        const delta = Math.min(Math.max(0.001, elapsed), MAX_FRAME_DELTA)
        this.physics.beginFrame()
        this.character.update(delta)

        // 每个节点一次派发，使 pass 顺序显式（skin → constraints →
        // collide → normals）；CPU 后端则不会产出节点。
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
      // 渲染守卫——首次错误即停止 ticker，避免下一帧重复同一异常；
      // 通过 $engineError 暴露给开发者覆盖层。
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
    // 重新选择像素比：window.devicePixelRatio 在跨不同 DPI 显示器拖动时会变化。
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
