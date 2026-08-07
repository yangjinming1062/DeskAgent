import * as THREE from 'three'
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js'

import type { SpriteEmotion, SpriteStateName } from '@/companion/companion-store'

import { resolveClip } from './AnimationMap'
import { MorphController } from './MorphController'
import type { LoadedModelInfo } from './types'

interface ProcParts {
  body: THREE.Mesh
  leftEye: THREE.Mesh
  rightEye: THREE.Mesh
  mouth: THREE.Mesh
  group: THREE.Group
}

const PBR_TEXTURE_KEYS = [
  'map', 'normalMap', 'roughnessMap', 'metalnessMap', 'aoMap',
  'emissiveMap', 'bumpMap', 'displacementMap'
] as const

export class CharacterController {
  private readonly morph = new MorphController()

  root = new THREE.Group()
  private mixer: THREE.AnimationMixer | null = null
  private actions = new Map<string, THREE.AnimationAction>()
  private actionNames = new Set<string>()
  private currentAction: THREE.AnimationAction | null = null
  private currentOutfitTex: THREE.Texture | null = null
  private isProcedural = false
  private proc: ProcParts | null = null

  private currentState: SpriteStateName = 'idle'
  private breathPhase = 0
  private lookX = 0
  private lookY = 0

  /** GLB: async-load model + animations; falls back to procedural on error. */
  async load(url: string | null, scene: THREE.Scene): Promise<LoadedModelInfo> {
    if (url) {
      try {
        this.disposeRoot(scene)
        const loader = new GLTFLoader()
        const gltf = await loader.loadAsync(url)
        this.root = gltf.scene
        this.root.traverse(child => {
          if (child instanceof THREE.Mesh) {
            child.castShadow = true
            child.receiveShadow = true
          }
        })
        scene.add(this.root)
        this.mixer = new THREE.AnimationMixer(this.root)
        for (const clip of gltf.animations) {
          this.actions.set(clip.name, this.mixer.clipAction(clip))
        }
        this.actionNames = new Set(this.actions.keys())
        this.morph.discover(this.root)
        this.applyState(this.currentState, null)
        return {
          hasMorphTargets: this.morph.hasTargets(),
          hasAnimations: this.actions.size > 0,
          clipNames: [...this.actions.keys()],
          morphNames: this.morph.targetNames()
        }
      } catch (err) {
        console.warn('[CharacterController] GLB load failed, using procedural fallback:', err)
      }
    }
    this.createProcedural(scene)
    return { hasMorphTargets: false, hasAnimations: false, clipNames: [], morphNames: [] }
  }

  private disposeRoot(scene: THREE.Scene | null): void {
    if (this.root.parent) scene?.remove(this.root)
    this.mixer?.stopAllAction()
    this.mixer = null
    this.actions.clear()
    this.actionNames.clear()
    this.currentOutfitTex?.dispose()
    this.currentOutfitTex = null
    this.isProcedural = false
    this.proc = null
    this.root.traverse(child => {
      if (child instanceof THREE.Mesh) {
        child.geometry?.dispose()
        const mats = Array.isArray(child.material) ? child.material : [child.material]
        for (const mat of mats) {
          if (!mat) continue
          // Dispose PBR textures before the material — the material.dispose()
          // call below doesn't release GPU texture refs.
          for (const key of PBR_TEXTURE_KEYS) {
            const tex = (mat as unknown as Record<string, THREE.Texture | null>)[key]
            if (tex) tex.dispose()
          }
          mat.dispose()
        }
      }
    })
    this.root = new THREE.Group()
  }

  /** Drive animation + morphs from the companion state machine. */
  applyState(state: SpriteStateName, emotion: SpriteEmotion | null): void {
    this.currentState = state

    if (!this.isProcedural && this.mixer) {
      const clipName = resolveClip(state, this.actionNames)
      if (clipName) this.playClip(clipName, 0.3)
    }

    this.morph.setExpression(state === 'emotional' ? emotion : null)
  }

  /** Audio amplitude [0..1] for TTS-driven lip sync. */
  setLipSyncAmplitude(amp: number): void {
    this.morph.setLipSyncAmplitude(amp)
    if (this.isProcedural && this.proc) {
      this.proc.mouth.scale.y = 1 + amp * 5
    }
  }

  /** Apply body-shape morph parameters (e.g. height, weight, face shape).
   * Keys are morph target names from the loaded GLB; values are 0.0–1.0. */
  setMorphs(params: Record<string, number>): void {
    this.root.traverse(child => {
      if (!(child instanceof THREE.Mesh)) return
      const dict = child.morphTargetDictionary
      const infls = child.morphTargetInfluences
      if (!dict || !infls) return
      for (const [name, value] of Object.entries(params)) {
        const idx = dict[name]
        if (idx !== undefined) infls[idx] = value
      }
    })
  }

  /** Hot-swap outfit: apply material color/roughness/metalness overrides to
   * all meshes (wildcard "*") or specific mesh names. Optionally load and
   * apply a generated texture as the albedo map. */
  setOutfit(item: {
    material_overrides: Record<string, { color?: string; roughness?: number; metalness?: number }>
    texture_url?: string | null
  }): void {
    if (this.isProcedural) return
    const overrides = item.material_overrides
    const wildcard = overrides['*']

    this.root.traverse(child => {
      if (!(child instanceof THREE.Mesh)) return
      const mat = child.material as THREE.MeshStandardMaterial
      if (!mat?.color) return
      const ov = overrides[child.name] ?? wildcard
      if (!ov) return
      if (ov.color) mat.color.set(ov.color)
      if (ov.roughness !== undefined) mat.roughness = ov.roughness
      if (ov.metalness !== undefined) mat.metalness = ov.metalness
    })

    if (item.texture_url) {
      new THREE.TextureLoader().load(item.texture_url, tex => {
        tex.colorSpace = THREE.SRGBColorSpace
        this.currentOutfitTex?.dispose()
        this.currentOutfitTex = tex
        this.root.traverse(child => {
          if (!(child instanceof THREE.Mesh)) return
          const m = child.material as THREE.MeshStandardMaterial
          if (m) { m.map = tex; m.needsUpdate = true }
        })
      })
    }
  }

  setLookTarget(nx: number, ny: number): void {
    // nx, ny normalised to [-1, 1] from screen centre
    this.lookX = THREE.MathUtils.clamp(nx, -1, 1)
    this.lookY = THREE.MathUtils.clamp(ny, -1, 1)
  }

  update(delta: number): void {
    this.breathPhase += delta
    this.mixer?.update(delta)
    this.morph.update(delta)

    if (this.isProcedural) {
      this.updateProcedural(delta)
    } else {
      // Subtle idle float for GLB characters whose clip may not include it
      this.root.position.y = Math.sin(this.breathPhase * 0.8) * 0.01
    }
    this.applyLookAt()
  }

  dispose(): void {
    this.disposeRoot(null)
  }

  private playClip(name: string, fade: number): void {
    const next = this.actions.get(name)
    if (!next || next === this.currentAction) return
    next.reset().setEffectiveWeight(1).setEffectiveTimeScale(1)
    this.currentAction?.crossFadeTo(next, fade, false)
    next.play()
    this.currentAction = next
  }

  private applyLookAt(): void {
    const yaw = this.lookX * 0.15
    const pitch = -this.lookY * 0.08
    this.root.rotation.y = THREE.MathUtils.lerp(this.root.rotation.y, yaw, 0.08)
    this.root.rotation.x = THREE.MathUtils.lerp(this.root.rotation.x, pitch, 0.08)
  }

  // ── Procedural fallback character ───────────────────────────

  private createProcedural(scene: THREE.Scene): void {
    this.isProcedural = true
    const group = new THREE.Group()

    const bodyGeo = new THREE.SphereGeometry(0.5, 48, 48)
    const bodyMat = new THREE.MeshStandardMaterial({
      color: 0xeae0d0,
      roughness: 0.55,
      metalness: 0.0
    })
    const body = new THREE.Mesh(bodyGeo, bodyMat)
    body.scale.set(0.82, 1.08, 0.82)
    body.position.y = 1.0
    body.castShadow = true
    body.receiveShadow = true
    group.add(body)

    const eyeGeo = new THREE.SphereGeometry(0.055, 20, 20)
    const eyeMat = new THREE.MeshStandardMaterial({ color: 0x1a1a2e, roughness: 0.12 })
    const leftEye = new THREE.Mesh(eyeGeo, eyeMat)
    leftEye.position.set(-0.13, 1.18, 0.38)
    group.add(leftEye)
    const rightEye = new THREE.Mesh(eyeGeo, eyeMat.clone())
    rightEye.position.set(0.13, 1.18, 0.38)
    group.add(rightEye)

    const mouthGeo = new THREE.BoxGeometry(0.1, 0.015, 0.02)
    const mouthMat = new THREE.MeshStandardMaterial({ color: 0x9a6b4a, roughness: 0.4 })
    const mouth = new THREE.Mesh(mouthGeo, mouthMat)
    mouth.position.set(0, 1.04, 0.4)
    group.add(mouth)

    this.proc = { body, leftEye, rightEye, mouth, group }
    this.root = group
    scene.add(group)
  }

  private updateProcedural(_delta: number): void {
    if (!this.proc) return
    const t = this.breathPhase
    const breath = Math.sin(t * 1.7) * 0.5 + 0.5

    // Reset transforms each frame
    this.proc.group.position.y = 0
    this.proc.body.scale.set(0.82, 1.08, 0.82)
    this.proc.body.rotation.z = 0
    this.proc.mouth.scale.y = 1

    switch (this.currentState) {
      case 'speaking': {
        this.proc.group.position.y = Math.sin(t * 5) * 0.015
        // Mouth scale driven by setLipSyncAmplitude, not sine wave
        break
      }
      case 'thinking': {
        this.proc.body.rotation.z = Math.sin(t * 0.8) * 0.08
        break
      }
      case 'sleeping': {
        this.proc.body.scale.set(0.8, 1.0 + breath * 0.01, 0.8)
        this.proc.leftEye.scale.y = 0.1
        this.proc.rightEye.scale.y = 0.1
        return // skip eye reset below
      }
      case 'working': {
        this.proc.group.position.y = Math.sin(t * 3) * 0.008
        break
      }
      case 'interacting': {
        this.proc.group.position.y = Math.abs(Math.sin(t * 4)) * 0.06
        break
      }
      case 'disconnected': {
        this.proc.body.rotation.z = -0.12
        break
      }
    }

    // Procedural blink — sleeping already returned early above
    const blinkCycle = t % (3 + (this.currentState.charCodeAt(0) % 3))
    const blinkWindow = blinkCycle > 2.8 && blinkCycle < 2.95
    const eyeScaleY = blinkWindow ? 0.1 : 1
    this.proc.leftEye.scale.y = eyeScaleY
    this.proc.rightEye.scale.y = eyeScaleY
  }
}