import * as THREE from 'three'
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js'

import type { SpriteEmotion, SpriteStateName } from '@/companion/companion-store'
import { safeJsonParse } from '@/shared/lib/safe-json'

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

interface OutfitItem {
  material_overrides_json?: string | null
  texture_url?: string | null
  normal_url?: string | null
  roughness_url?: string | null
  metalness_url?: string | null
}

// Channels the wardrobe pipeline can populate on a WardrobeItem. The keys
// are the URL field names on the JSON response; the values name the
// MeshStandardMaterial slot each texture binds to. Adding a new channel
// means a new field on the ORM + schema + this table.
type PbrChannel = 'albedo' | 'normal' | 'roughness' | 'metalness'

const PBR_CHANNEL_DEFS: Record<
  PbrChannel,
  {
    urlField: keyof OutfitItem
    slot: 'map' | 'normalMap' | 'roughnessMap' | 'metalnessMap'
    colorSpace: THREE.ColorSpace
  }
> = {
  albedo: { urlField: 'texture_url', slot: 'map', colorSpace: THREE.SRGBColorSpace },
  normal: { urlField: 'normal_url', slot: 'normalMap', colorSpace: THREE.NoColorSpace },
  roughness: { urlField: 'roughness_url', slot: 'roughnessMap', colorSpace: THREE.NoColorSpace },
  metalness: { urlField: 'metalness_url', slot: 'metalnessMap', colorSpace: THREE.NoColorSpace }
}

const PBR_TEXTURE_KEYS = [
  'map',
  'normalMap',
  'roughnessMap',
  'metalnessMap',
  'aoMap',
  'emissiveMap',
  'bumpMap',
  'displacementMap'
] as const

export class CharacterController {
  private readonly morph = new MorphController()

  root = new THREE.Group()
  private mixer: THREE.AnimationMixer | null = null
  private actions = new Map<string, THREE.AnimationAction>()
  private actionNames = new Set<string>()
  private currentAction: THREE.AnimationAction | null = null
  private isProcedural = false
  private proc: ProcParts | null = null

  private currentState: SpriteStateName = 'idle'
  private breathPhase = 0
  private lookX = 0
  private lookY = 0
  // Track PBR channel textures we last applied so a hot-swap can dispose the previous set before replacing it.
  private currentPbrTex: Record<PbrChannel, THREE.Texture | null> = {
    albedo: null,
    normal: null,
    roughness: null,
    metalness: null
  }
  // Monotonic epoch so stale textureLoader callbacks (e.g. reverse load-completion order on rapid setOutfit) dispose their texture and bail.
  private textureEpoch = 0
  private readonly textureLoader = new THREE.TextureLoader()

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
    // Bump epoch first so in-flight textureLoader callbacks dispose their freshly-decoded texture and bail.
    this.textureEpoch++

    if (this.root.parent) {
      scene?.remove(this.root)
    }

    this.mixer?.stopAllAction()
    this.mixer = null
    this.actions.clear()
    this.actionNames.clear()

    for (const channel of Object.keys(this.currentPbrTex) as PbrChannel[]) {
      this.currentPbrTex[channel]?.dispose()
      this.currentPbrTex[channel] = null
    }

    this.isProcedural = false
    this.proc = null
    this.root.traverse(child => {
      if (child instanceof THREE.Mesh) {
        child.geometry?.dispose()
        const mats = Array.isArray(child.material) ? child.material : [child.material]

        for (const mat of mats) {
          if (!mat) {
            continue
          }

          // Dispose PBR textures before the material — material.dispose() doesn't release GPU texture refs.
          // currentPbrTex tracks the setOutfit-loaded ones (disposed above); this sweep also covers GLB-baked textures that live only on materials. dispose() is idempotent.
          for (const key of PBR_TEXTURE_KEYS) {
            const tex = (mat as unknown as Record<string, THREE.Texture | null>)[key]

            if (tex) {
              tex.dispose()
            }
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

      if (clipName) {
        this.playClip(clipName, 0.25)
      }
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
      if (!(child instanceof THREE.Mesh)) {
        return
      }

      const dict = child.morphTargetDictionary
      const infls = child.morphTargetInfluences

      if (!dict || !infls) {
        return
      }

      for (const [name, value] of Object.entries(params)) {
        const idx = dict[name]

        if (idx !== undefined) {
          infls[idx] = value
        }
      }
    })
  }

  /** Hot-swap outfit. A missing PBR channel clears the prior binding so preset items don't show stale textures. */
  setOutfit(item: OutfitItem): void {
    if (this.isProcedural) {
      return
    }

    // Invalidate in-flight loadPbrChannel callbacks from a previous setOutfit.
    this.textureEpoch++

    const parsed = safeJsonParse<unknown>(item.material_overrides_json, {})

    const overrides: Record<string, { color?: string; roughness?: number; metalness?: number }> =
      parsed && typeof parsed === 'object' && !Array.isArray(parsed)
        ? (parsed as Record<string, { color?: string; roughness?: number; metalness?: number }>)
        : {}

    const wildcard = overrides['*']

    this.root.traverse(child => {
      if (!(child instanceof THREE.Mesh)) {
        return
      }

      const mat = child.material as THREE.MeshStandardMaterial

      if (!mat?.color) {
        return
      }

      const ov = overrides[child.name] ?? wildcard

      if (!ov) {
        return
      }

      if (ov.color) {
        mat.color.set(ov.color)
      }

      if (ov.roughness !== undefined) {
        mat.roughness = ov.roughness
      }

      if (ov.metalness !== undefined) {
        mat.metalness = ov.metalness
      }
    })

    for (const channel of Object.keys(PBR_CHANNEL_DEFS) as PbrChannel[]) {
      const def = PBR_CHANNEL_DEFS[channel]
      const url = item[def.urlField]

      if (!url) {
        this.clearPbrChannel(channel, def.slot)

        continue
      }

      this.loadPbrChannel(url, channel, def.colorSpace, def.slot)
    }
  }

  /** Load one PBR channel texture and bind it. Captures ``textureEpoch``; stale callbacks dispose and bail. */
  private loadPbrChannel(
    url: string,
    channel: PbrChannel,
    colorSpace: THREE.ColorSpace,
    slot: 'map' | 'normalMap' | 'roughnessMap' | 'metalnessMap'
  ): void {
    const epoch = this.textureEpoch
    this.textureLoader.load(url, tex => {
      // Stale callback: a newer setOutfit / disposeRoot invalidated this load.
      // Dispose the freshly-decoded texture (never bound to a mesh) and bail.
      if (epoch < this.textureEpoch) {
        tex.dispose()

        return
      }

      tex.colorSpace = colorSpace
      this.currentPbrTex[channel]?.dispose()
      this.currentPbrTex[channel] = tex
      this.root.traverse(child => {
        if (!(child instanceof THREE.Mesh)) {
          return
        }

        const m = child.material as THREE.MeshStandardMaterial

        if (m) {
          ;(m as unknown as Record<string, THREE.Texture | null>)[slot] = tex
          m.needsUpdate = true
        }
      })
    })
  }

  /** Clear the texture slot on every mesh for a PBR channel and dispose the previously-bound texture. */
  private clearPbrChannel(channel: PbrChannel, slot: 'map' | 'normalMap' | 'roughnessMap' | 'metalnessMap'): void {
    const previous = this.currentPbrTex[channel]

    if (previous) {
      this.currentPbrTex[channel] = null
      previous.dispose()
    }

    this.root.traverse(child => {
      if (!(child instanceof THREE.Mesh)) {
        return
      }

      const m = child.material as THREE.MeshStandardMaterial

      if (m) {
        ;(m as unknown as Record<string, THREE.Texture | null>)[slot] = null
        m.needsUpdate = true
      }
    })
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

    if (!next || next === this.currentAction) {
      return
    }

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
    if (!this.proc) {
      return
    }

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
