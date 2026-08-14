import * as THREE from 'three'
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js'
import { PMREMGenerator as WebGPUPMREMGenerator, WebGPURenderer } from 'three/webgpu'

/** Three-point lighting + PMREM environment for PBR material reflections.
 * Tuned for a realistic character bust/half-body framing. */

type RendererHost = THREE.WebGLRenderer | WebGPURenderer

export class LightingRig {
  private readonly ambient: THREE.AmbientLight
  private readonly key: THREE.DirectionalLight
  private readonly fill: THREE.DirectionalLight
  private readonly rim: THREE.DirectionalLight

  // The env texture is the only part that survives construction; the classic
  // PMREMGenerator is bound to WebGLRenderer internals and the webgpu one to
  // the WebGPU backend, so the target handle differs per renderer kind.
  private readonly envTexture: THREE.Texture
  private readonly disposeEnvTarget: () => void

  constructor(scene: THREE.Scene, renderer: RendererHost) {
    // PMREM environment gives PBR materials realistic ambient reflections
    // without needing an HDRI file.
    if (renderer instanceof WebGPURenderer) {
      const pmrem = new WebGPUPMREMGenerator(renderer)
      const target = pmrem.fromScene(new RoomEnvironment(), 0.04)
      this.envTexture = target.texture
      this.disposeEnvTarget = () => target.dispose()
      pmrem.dispose()
    } else {
      const pmrem = new THREE.PMREMGenerator(renderer)
      const target = pmrem.fromScene(new RoomEnvironment(), 0.04)
      this.envTexture = target.texture
      this.disposeEnvTarget = () => target.dispose()
      pmrem.dispose()
    }

    scene.environment = this.envTexture

    this.ambient = new THREE.AmbientLight(0xffffff, 0.15)
    scene.add(this.ambient)

    // Key — warm, front-left, casts the primary shadow.
    this.key = new THREE.DirectionalLight(0xfff2e6, 2.0)
    this.key.position.set(1.8, 2.5, 3.0)
    this.key.castShadow = true
    this.key.shadow.mapSize.set(2048, 2048)
    this.key.shadow.camera.near = 0.5
    this.key.shadow.camera.far = 12
    this.key.shadow.camera.left = -2.5
    this.key.shadow.camera.right = 2.5
    this.key.shadow.camera.top = 2.5
    this.key.shadow.camera.bottom = -1.5
    this.key.shadow.bias = -0.0005
    this.key.shadow.radius = 4
    scene.add(this.key)

    // Fill — cool, front-right, softer; lifts shadow detail.
    this.fill = new THREE.DirectionalLight(0xc4d8ff, 0.6)
    this.fill.position.set(-2.0, 1.2, 2.5)
    scene.add(this.fill)

    // Rim — behind/above, creates edge separation from background.
    this.rim = new THREE.DirectionalLight(0xe8d8ff, 0.9)
    this.rim.position.set(0.5, 3.0, -3.5)
    scene.add(this.rim)
  }

  dispose(scene: THREE.Scene): void {
    scene.environment = null
    this.disposeEnvTarget()
    scene.remove(this.ambient, this.key, this.fill, this.rim)
  }
}
