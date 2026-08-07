import * as THREE from 'three'

import { CharacterController } from './CharacterController'
import { LightingRig } from './LightingRig'
import type { EngineOptions, LoadedModelInfo } from './types'

export class Engine {
  readonly renderer: THREE.WebGLRenderer
  readonly scene: THREE.Scene
  readonly camera: THREE.PerspectiveCamera
  readonly clock = new THREE.Clock()
  readonly character = new CharacterController()
  readonly lighting: LightingRig

  private rafId: number | null = null
  private disposed = false

  constructor(opts: EngineOptions) {
    this.renderer = new THREE.WebGLRenderer({
      canvas: opts.canvas,
      alpha: true,
      antialias: true,
      powerPreference: 'high-performance'
    })
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    this.renderer.setSize(opts.width, opts.height)
    this.renderer.setClearColor(0x000000, 0)
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping
    this.renderer.toneMappingExposure = 1.0
    this.renderer.outputColorSpace = THREE.SRGBColorSpace
    this.renderer.shadowMap.enabled = true
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap

    this.scene = new THREE.Scene()

    // Bust/half-body framing for a desktop companion.
    this.camera = new THREE.PerspectiveCamera(35, opts.width / opts.height, 0.1, 50)
    this.camera.position.set(0, 1.35, 2.8)
    this.camera.lookAt(0, 1.15, 0)

    this.lighting = new LightingRig(this.scene, this.renderer)
  }

  async loadCharacter(url: string | null): Promise<LoadedModelInfo> {
    return this.character.load(url, this.scene)
  }

  start(): void {
    if (this.rafId !== null) {
      return
    }

    const loop = () => {
      if (this.disposed) {
        return
      }

      this.rafId = requestAnimationFrame(loop)
      const delta = this.clock.getDelta()
      this.character.update(delta)
      this.renderer.render(this.scene, this.camera)
    }

    this.rafId = requestAnimationFrame(loop)
  }

  stop(): void {
    if (this.rafId !== null) {
      cancelAnimationFrame(this.rafId)
      this.rafId = null
    }
  }

  resize(width: number, height: number): void {
    // Re-pick pixel ratio: window.devicePixelRatio changes when the window
    // is dragged across monitors with different DPI.
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    this.camera.aspect = width / height
    this.camera.updateProjectionMatrix()
    this.renderer.setSize(width, height)
  }

  dispose(): void {
    this.disposed = true
    this.stop()
    this.lighting.dispose(this.scene)
    this.character.dispose()
    this.renderer.dispose()
  }
}
