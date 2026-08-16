import * as THREE from 'three'
import { MeshStandardNodeMaterial } from 'three/webgpu'

import type { SpriteEmotion, SpriteStateName } from '@/companion/companion-store'
import { log } from '@/shared/lib/log'
import { safeJsonParse } from '@/shared/lib/safe-json'
import type { ReactionBucket } from '@/shared/types/reactions'

import { resolveClip } from './AnimationMap'
import { BodyCollider } from './BodyCollider'
import { resolveEmotionClip, resolveInteractionClip } from './clip-dispatch'
import { buildClip, type ClipDef } from './clips-biped'
import { buildClipsForRig, getClipDefs } from './clips-registry'
import { hasGltf, stashGltf, takeGltfClone } from './gltf-instance-cache'
import { createGLTFLoader } from './gltf-loader-factory'
import { $availableClipNames, type CompanionExpression } from './model-store'
import { MorphController } from './MorphController'
import type { PhysicsBackend, PhysicsUnit } from './physics/PhysicsBackend'
import { type LoadedModelInfo } from './types'

interface ProcParts {
  body: THREE.Mesh
  leftEye: THREE.Mesh
  rightEye: THREE.Mesh
  mouth: THREE.Mesh
  cracks?: THREE.Line[]
  crackMats?: THREE.LineBasicMaterial[]
  group: THREE.Group
}

interface OutfitItem {
  material_overrides_json?: string | null
  texture_url?: string | null
  normal_url?: string | null
  roughness_url?: string | null
  metalness_url?: string | null
  displacement_url?: string | null
  // Geometric wardrobe (PROTOCOL.md §1.6 + companion README §9).
  kind?: string
  mesh_url?: string | null
  assembly_json?: string
}

interface AssemblySpec {
  kind: string
  layer: number
  socket: string | null
  physics: string
}

interface AssembledUnit {
  group: THREE.Group
  physics: PhysicsUnit[]
  key: string
  anchors?: THREE.Group[]
}

function isTextureItem(item: OutfitItem): boolean {
  return !item.mesh_url || (item.kind ?? 'texture') === 'texture'
}

function parseAssembly(item: OutfitItem): AssemblySpec {
  const parsed = safeJsonParse<unknown>(item.assembly_json ?? '{}', {})

  const asm =
    parsed && typeof parsed === 'object' && !Array.isArray(parsed)
      ? (parsed as Partial<Record<keyof AssemblySpec, unknown>>)
      : {}

  return {
    kind: typeof asm.kind === 'string' ? asm.kind : (item.kind ?? 'texture'),
    layer: typeof asm.layer === 'number' ? asm.layer : 1,
    socket: typeof asm.socket === 'string' ? asm.socket : null,
    physics: asm.physics === 'cloth' ? 'cloth' : 'skin'
  }
}

// Channels the wardrobe pipeline can populate on a WardrobeItem. The keys
// are the URL field names on the JSON response; the values name the
// MeshStandardMaterial slot each texture binds to. Adding a new channel
// means a new field on the ORM + schema + this table.
type PbrChannel = 'albedo' | 'normal' | 'roughness' | 'metalness' | 'displacement'

type PbrSlot =
  | 'map'
  | 'normalMap'
  | 'roughnessMap'
  | 'metalnessMap'
  | 'aoMap'
  | 'emissiveMap'
  | 'bumpMap'
  | 'displacementMap'

// Cloth-unit materials become MeshStandardNodeMaterial under the TSL physics
// backend — both expose the same PBR slots, so binds and disposal accept both.
type PbrMaterial = THREE.MeshStandardMaterial | MeshStandardNodeMaterial

const isPbrMaterial = (m: THREE.Material): m is PbrMaterial =>
  m instanceof THREE.MeshStandardMaterial || m instanceof MeshStandardNodeMaterial

const getPbrSlot = (mat: PbrMaterial, slot: PbrSlot): THREE.Texture | null => mat[slot]

const setPbrSlot = (mat: PbrMaterial, slot: PbrSlot, tex: THREE.Texture | null): void => {
  mat[slot] = tex

  if (tex) {
    mat.needsUpdate = true
  }
}

const PBR_CHANNEL_DEFS: Record<
  PbrChannel,
  {
    urlField: keyof OutfitItem
    slot: Exclude<PbrSlot, 'aoMap' | 'emissiveMap' | 'bumpMap'>
    colorSpace: THREE.ColorSpace
  }
> = {
  albedo: { urlField: 'texture_url', slot: 'map', colorSpace: THREE.SRGBColorSpace },
  normal: { urlField: 'normal_url', slot: 'normalMap', colorSpace: THREE.NoColorSpace },
  roughness: { urlField: 'roughness_url', slot: 'roughnessMap', colorSpace: THREE.NoColorSpace },
  metalness: { urlField: 'metalness_url', slot: 'metalnessMap', colorSpace: THREE.NoColorSpace },
  displacement: { urlField: 'displacement_url', slot: 'displacementMap', colorSpace: THREE.NoColorSpace }
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

// Dispose geometry, materials, and textures under an Object3D hierarchy.
const disposeObjectTree = (root: THREE.Object3D): void => {
  root.traverse(child => {
    if (child instanceof THREE.Mesh || child instanceof THREE.Line || child instanceof THREE.LineSegments) {
      child.geometry?.dispose()
      const mats = Array.isArray(child.material) ? child.material : [child.material]

      for (const mat of mats) {
        if (!mat) {
          continue
        }

        // Dispose PBR textures before the material — material.dispose() doesn't release GPU texture refs.
        // currentPbrTex tracks the setOutfit-loaded ones (disposed by the caller); this sweep also covers
        // GLB-baked textures that live only on materials. dispose() is idempotent.
        if (isPbrMaterial(mat)) {
          for (const key of PBR_TEXTURE_KEYS) {
            const tex = getPbrSlot(mat, key)

            if (tex) {
              tex.dispose()
            }
          }
        }

        mat.dispose()
      }
    }
  })
}

/**
 * Transparently decompresses Gzip / Deflate compressed GLB buffers.
 * Preserves 100% full mesh resolution and fidelity while drastically reducing transport size.
 */
async function decompressGlbIfNeeded(buffer: ArrayBuffer): Promise<ArrayBuffer> {
  const bytes = new Uint8Array(buffer)

  // Gzip magic bytes (0x1f, 0x8b)
  if (bytes.length >= 2 && bytes[0] === 0x1f && bytes[1] === 0x8b) {
    if (typeof DecompressionStream !== 'undefined') {
      try {
        const ds = new DecompressionStream('gzip')
        const decompressed = await new Response(new Response(bytes).body?.pipeThrough(ds)).arrayBuffer()

        return decompressed
      } catch (err) {
        log.warn('CharacterController', 'Failed to decompress gzip glb buffer:', err)
      }
    }
  }

  // Deflate / zlib magic bytes (0x78 0x9c / 0x78 0x01 / 0x78 0xda)
  if (bytes.length >= 2 && bytes[0] === 0x78 && (bytes[1] === 0x9c || bytes[1] === 0x01 || bytes[1] === 0xda)) {
    if (typeof DecompressionStream !== 'undefined') {
      try {
        const ds = new DecompressionStream('deflate')
        const decompressed = await new Response(new Response(bytes).body?.pipeThrough(ds)).arrayBuffer()

        return decompressed
      } catch (err) {
        log.warn('CharacterController', 'Failed to decompress deflate glb buffer:', err)
      }
    }
  }

  return buffer
}

const _QUAT = new THREE.Quaternion()
const _EULER = new THREE.Euler()

export class CharacterController {
  private readonly morph = new MorphController()
  private readonly physics: PhysicsBackend

  root = new THREE.Group()
  private mixer: THREE.AnimationMixer | null = null
  private actions = new Map<string, THREE.AnimationAction>()
  private actionNames = new Set<string>()
  private injectedClipDefs: ClipDef[] = []
  private currentAction: THREE.AnimationAction | null = null
  private isProcedural = false
  private proc: ProcParts | null = null
  private rigType: string = 'biped'
  private headBone: THREE.Bone | null = null
  private neckBone: THREE.Bone | null = null

  get isBipedRig(): boolean {
    return this.rigType === 'biped'
  }

  private currentState: SpriteStateName = 'idle'
  private breathPhase = 0
  private lookX = 0
  private lookY = 0
  // Track PBR channel textures we last applied so a hot-swap can dispose the previous set before replacing it.
  private currentPbrTex: Record<PbrChannel, THREE.Texture | null> = {
    albedo: null,
    normal: null,
    roughness: null,
    metalness: null,
    displacement: null
  }
  // Monotonic epoch so stale textureLoader callbacks (e.g. reverse load-completion order on rapid setOutfit) dispose their texture and bail.
  private textureEpoch = 0
  private readonly textureLoader = new THREE.TextureLoader()

  // Assembled wardrobe units (PROTOCOL.md §1.6): one entry per equipped
  // geometric item (garment or accessory); texture items apply to the body
  // root instead and keep no entry here.
  private units: AssembledUnit[] = []
  private bodyCollider: BodyCollider | null = null
  private boneRestQuats = new Map<string, THREE.Quaternion>()

  constructor(physics: PhysicsBackend) {
    this.physics = physics
  }

  /** Parse pre-fetched GLB bytes + animations; falls back to procedural on error. Bytes arrive from the renderer's `apiAssetBuffer` IPC (host-stripped + re-based by main, no CORS preflight). When `contentHash` is provided and `gltf-instance-cache` already has a parsed template, the load pulls a deep clone instead of re-parsing. */
  async load(
    bytes: ArrayBuffer | null,
    scene: THREE.Scene,
    rigType: string = 'biped',
    contentHash?: string
  ): Promise<LoadedModelInfo> {
    this.rigType = rigType

    if (bytes || (contentHash && hasGltf(contentHash))) {
      try {
        this.disposeRoot(scene)

        let rootScene: THREE.Group
        let gltfAnimations: THREE.AnimationClip[]

        // Detached cache: deep clone is dramatically cheaper than re-parsing GLB.
        if (contentHash && hasGltf(contentHash)) {
          const cached = takeGltfClone(contentHash)!

          rootScene = cached.scene
          gltfAnimations = cached.animations
        } else {
          if (!bytes) {
            throw new Error('no bytes and no cache hit')
          }

          const decompressedBytes = await decompressGlbIfNeeded(bytes)
          const loader = createGLTFLoader()
          const gltf = await loader.parseAsync(decompressedBytes, '')
          rootScene = gltf.scene
          gltfAnimations = gltf.animations

          // Template stays owned by the cache; subsequent takes return deep clones.
          if (contentHash) {
            stashGltf(contentHash, gltf.scene, gltf.animations, bytes.byteLength)
          }
        }

        this.root = rootScene
        this.root.traverse(child => {
          if (child instanceof THREE.Mesh) {
            child.castShadow = true
            child.receiveShadow = true
            const mats = Array.isArray(child.material) ? child.material : [child.material]

            for (const m of mats) {
              if (m && isPbrMaterial(m)) {
                m.userData = m.userData || {}
                m.userData.baseMap = m.map
                m.userData.baseNormalMap = m.normalMap
                m.userData.baseRoughnessMap = m.roughnessMap
                m.userData.baseMetalnessMap = m.metalnessMap
              }
            }
          }
        })
        scene.add(this.root)
        this.mixer = new THREE.AnimationMixer(this.root)
        this.mixer.addEventListener('finished', () => {
          if (this.mixer && !this.isProcedural) {
            const baseClip = resolveClip(this.currentState, this.actionNames)

            if (baseClip && this.actionNames.has(baseClip)) {
              this.playClip(baseClip, 0.3)
            }
          }
        })

        for (const clip of gltfAnimations) {
          this.actions.set(clip.name, this.mixer.clipAction(clip))
        }

        this.headBone = null
        this.neckBone = null
        this.boneRestQuats.clear()
        this.root.traverse(child => {
          if (child instanceof THREE.Bone) {
            this.boneRestQuats.set(child.name, child.quaternion.clone())
            const name = child.name.toLowerCase()

            if (child.name === 'Head' || child.name === 'mixamorigHead' || name.endsWith('head')) {
              this.headBone = child
            } else if (child.name === 'Neck' || child.name === 'mixamorigNeck' || name.endsWith('neck')) {
              this.neckBone = child
            }
          }
        })

        for (const clip of buildClipsForRig(rigType, this.boneRestQuats)) {
          this.actions.set(clip.name, this.mixer.clipAction(clip))
        }

        for (const def of this.injectedClipDefs) {
          const clip = buildClip(def, this.boneRestQuats)
          this.actions.set(clip.name, this.mixer.clipAction(clip))
        }

        this.actionNames = new Set(this.actions.keys())
        $availableClipNames.set(new Set(this.actionNames))
        this.morph.discover(this.root)
        this.applyState(this.currentState, null)

        return {
          hasMorphTargets: this.morph.hasTargets(),
          hasAnimations: this.actions.size > 0,
          clipNames: [...this.actions.keys()],
          morphNames: this.morph.targetNames(),
          procedural: false
        }
      } catch (err) {
        log.warn('character', 'GLB load failed, using procedural fallback:', err)
      }
    }

    this.createProcedural(scene)

    return { hasMorphTargets: false, hasAnimations: false, clipNames: [], morphNames: [], procedural: true }
  }

  setCustomExpressions(exprs: readonly { name: string; weights: Record<string, number> }[]): void {
    this.morph.setCustomExpressions(exprs)
  }

  private disposeRoot(scene: THREE.Scene | null): void {
    // Bump epoch first so in-flight textureLoader callbacks dispose their freshly-decoded texture and bail.
    this.textureEpoch++

    this.headBone = null
    this.neckBone = null

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

    if (this.proc?.cracks) {
      this.proc.cracks.forEach(c => c.geometry?.dispose())
    }

    if (this.proc?.crackMats) {
      this.proc.crackMats.forEach(m => m.dispose())
    }

    this.isProcedural = false
    this.proc = null

    for (const unit of this.units) {
      this.disposeUnit(unit)
    }

    this.units = []
    this.bodyCollider = null
    this.boneRestQuats.clear()
    disposeObjectTree(this.root)
    this.root = new THREE.Group()
  }

  /** 运行时注入 clip 定义（不重载模型）。 */
  appendClipDefs(defs: readonly ClipDef[]): void {
    for (const def of defs) {
      this.injectedClipDefs = this.injectedClipDefs.filter(d => d.name !== def.name)
      this.injectedClipDefs.push(def)

      if (this.mixer) {
        const clip = buildClip(def, this.boneRestQuats)
        this.actions.set(clip.name, this.mixer.clipAction(clip))
        this.actionNames.add(clip.name)
      }
    }

    $availableClipNames.set(new Set(this.actionNames))
  }

  /** 移除指定的动态动作。 */
  removeClip(name: string): void {
    this.injectedClipDefs = this.injectedClipDefs.filter(d => d.name !== name)
    const action = this.actions.get(name)

    if (action) {
      action.stop()
      this.mixer?.uncacheClip(action.getClip())
      this.actions.delete(name)
      this.actionNames.delete(name)
      $availableClipNames.set(new Set(this.actionNames))
    }
  }

  /** 查询当前所有已注册的动作名称。 */
  get availableClipNames(): Set<string> {
    return new Set(this.actionNames)
  }

  /** 按名称播放指定动作。 */
  playClipByName(name: string, fade = 0.25): boolean {
    if (!this.actionNames.has(name)) {
      return false
    }

    this.playClip(name, fade)

    return true
  }

  applyState(
    state: SpriteStateName,
    emotion: SpriteEmotion | null,
    ctx?: {
      companionTags?: string[]
      interactionBucket?: ReactionBucket
      clipOverride?: string | null
      customExpressions?: CompanionExpression[]
      action?: string | null
    }
  ): void {
    this.currentState = state

    if (!this.isProcedural && this.mixer) {
      const tags = ctx?.companionTags ?? []
      const library = getClipDefs(this.rigType)
      const available = this.actionNames
      let clipName: string | null = null

      if (ctx?.clipOverride && this.actionNames.has(ctx.clipOverride)) {
        // Caller already resolved and verified the override (e.g. interaction.ts)
        clipName = ctx.clipOverride
      } else if (ctx?.interactionBucket) {
        // Tag-driven interaction clip selection
        clipName = resolveInteractionClip(ctx.interactionBucket, tags, library, available)
      } else if (state === 'emotional' && emotion) {
        // Tag-driven emotion clip selection
        clipName = resolveEmotionClip(emotion, tags, library, available, ctx?.customExpressions, ctx?.action)
      }

      // Spec state→clip map (MODEL_SPEC §3); last resort when no override / interaction / emotion clip resolves.
      if (!clipName) {
        clipName = resolveClip(state, this.actionNames)
      }

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

  /** Apply body-shape morph target weights (0.0–1.0). */
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

  /** Apply equipped wardrobe set (texture hot-swap and geometric assembly). */
  setOutfit(items: readonly OutfitItem[]): void {
    if (this.isProcedural) {
      return
    }

    this.textureEpoch++

    const textureItem = items.find(isTextureItem) ?? null
    this.applyTextureOutfit(textureItem)

    const geometric = items
      .filter(i => !isTextureItem(i))
      .map(i => ({ item: i, spec: parseAssembly(i) }))
      .sort((a, b) => a.spec.layer - b.spec.layer)

    const prevByKey = new Map(this.units.map(u => [u.key, u]))
    const keptKeys = new Set<string>()
    const seenKeys = new Set<string>()

    for (const { item, spec } of geometric) {
      const key = item.mesh_url ?? ''

      if (!key || seenKeys.has(key)) {
        continue
      }

      seenKeys.add(key)

      if (prevByKey.has(key)) {
        keptKeys.add(key)

        continue
      }

      void this.assembleUnit(item, spec)
    }

    const keptUnits: AssembledUnit[] = []

    for (const [key, unit] of prevByKey) {
      if (keptKeys.has(key)) {
        keptUnits.push(unit)
      } else {
        this.disposeUnit(unit)
      }
    }

    this.units = keptUnits
  }

  private applyTextureOutfit(item: OutfitItem | null): void {
    if (item === null) {
      // No texture item equipped — clear stale body PBR bindings left by the
      // previous outfit (the stub-object path accidentally also cleared, but
      // here it's an explicit operation).
      for (const channel of Object.keys(PBR_CHANNEL_DEFS) as PbrChannel[]) {
        const def = PBR_CHANNEL_DEFS[channel]
        this.clearPbrChannel(channel, def.slot)
      }

      return
    }

    const parsed = safeJsonParse<unknown>(item.material_overrides_json, {})

    const overrides: Record<string, { color?: string; roughness?: number; metalness?: number }> =
      parsed && typeof parsed === 'object' && !Array.isArray(parsed)
        ? (parsed as Record<string, { color?: string; roughness?: number; metalness?: number }>)
        : {}

    const wildcard = overrides['*']

    this.root.traverse(child => {
      if (!(child instanceof THREE.Mesh) || this.isUnitDescendant(child)) {
        return
      }

      const mats = Array.isArray(child.material) ? child.material : [child.material]

      for (const mat of mats) {
        if (!(mat instanceof THREE.MeshStandardMaterial) || !mat.color) {
          continue
        }

        const ov = overrides[child.name] ?? wildcard

        if (!ov) {
          continue
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
      }
    })

    this.bindPbrChannels(item)
  }

  private isUnitDescendant(obj: THREE.Object3D): boolean {
    let cur: THREE.Object3D | null = obj

    while (cur && cur !== this.root) {
      if (cur.name.startsWith('wardrobe-unit-')) {
        return true
      }

      cur = cur.parent
    }

    return false
  }

  /** Assemble a geometric unit (garment or accessory). */
  private async assembleUnit(item: OutfitItem, spec: AssemblySpec): Promise<void> {
    const epoch = this.textureEpoch
    const desktop = window.spiritagent

    let bytes: ArrayBuffer | null = null

    try {
      const u8 = await desktop.apiAssetBuffer({ url: item.mesh_url! })
      bytes = u8.slice().buffer
    } catch (err) {
      log.warn('character', 'unit GLB fetch failed:', err)

      return
    }

    if (epoch < this.textureEpoch || !bytes) {
      return
    }

    let gltf: { scene: THREE.Group }

    try {
      const loader = createGLTFLoader()
      gltf = await loader.parseAsync(bytes, '')
    } catch (err) {
      log.warn('character', 'unit GLB parse failed:', err)
      this.applyTextureOutfit(item)

      return
    }

    if (epoch < this.textureEpoch) {
      disposeObjectTree(gltf.scene)

      return
    }

    const group = new THREE.Group()
    group.name = `wardrobe-unit-${spec.kind}`
    let meshes: THREE.Mesh[] = []
    const physics: PhysicsUnit[] = []
    const anchors: THREE.Group[] = []

    if (spec.kind === 'accessory') {
      meshes = this.collectUnitMeshes(gltf.scene, false)
      const socketBone = spec.socket ? this.findBodyBone(spec.socket) : null

      if (socketBone) {
        // Parent an anchor to the socket bone, compensated so meshes keep
        // their authored world placement in rest pose while inheriting bone motion.
        const anchor = new THREE.Group()
        anchor.name = `wardrobe-unit-anchor-${spec.kind}`

        socketBone.add(anchor)
        const bodySkeleton = this.bodySkinnedMesh()?.skeleton
        const boneIdx = bodySkeleton ? bodySkeleton.bones.indexOf(socketBone) : -1

        if (boneIdx >= 0 && bodySkeleton?.boneInverses[boneIdx]) {
          anchor.matrix.copy(bodySkeleton.boneInverses[boneIdx])
        } else {
          socketBone.updateWorldMatrix(true, false)
          anchor.matrix.copy(socketBone.matrixWorld).invert()
        }

        anchor.matrix.decompose(anchor.position, anchor.quaternion, anchor.scale)

        for (const mesh of meshes) {
          anchor.add(mesh)
        }

        anchors.push(anchor)
      } else {
        log.warn('character', `accessory socket '${spec.socket ?? ''}' not found — attaching statically`)

        for (const mesh of meshes) {
          group.add(mesh)
        }
      }
    } else {
      // Garment path: find body SkinnedMesh for skeleton + bindMatrix.
      const bodyMesh = this.bodySkinnedMesh()

      if (!bodyMesh?.skeleton) {
        log.warn('character', 'no body SkinnedMesh found for garment rebind')
        this.applyTextureOutfit(item)

        return
      }

      const skinned = this.collectUnitMeshes(gltf.scene, true) as THREE.SkinnedMesh[]
      const bodyBoneNames: string[] = bodyMesh.skeleton.bones.map(b => b.name)

      for (const mesh of skinned) {
        this.rebindGarmentMesh(mesh, bodyMesh.skeleton, bodyMesh.bindMatrix, bodyBoneNames)
      }

      if (skinned.length > 0) {
        // Both cloth and skin units render as plain Meshes (no GPU skinning)
        // so the solver-written positions are authoritative — the TSL backend
        // achieves this via node-material positionNode instead of attribute
        // writes. Cloth hands free vertices to the verlet solver; skin pins
        // every vertex against the body to stop animation-time clipping.
        const bodyCollider = this.physics.kind === 'cpu' ? this.ensureBodyCollider(bodyMesh) : null
        const plain: THREE.Mesh[] = []

        for (const sk of skinned) {
          const plainMesh = new THREE.Mesh(sk.geometry, sk.material)

          plainMesh.castShadow = true
          plainMesh.receiveShadow = true
          plain.push(plainMesh)
          group.add(plainMesh)

          const unit = this.physics.createUnit({
            mesh: plainMesh,
            skeleton: bodyMesh.skeleton,
            bindMatrix: sk.bindMatrix,
            mode: spec.physics === 'cloth' ? 'cloth' : 'skin',
            bodyCollider
          })

          if (unit) {
            physics.push(unit)
          }
        }

        meshes = plain
      } else {
        for (const mesh of skinned) {
          group.add(mesh)
        }

        meshes = skinned
      }
    }

    this.root.add(group)
    this.units.push({ group, physics, key: item.mesh_url ?? '', anchors })

    // Bind PBR textures scoped to the unit's meshes only.
    this.bindPbrChannels(item, meshes)
  }

  /** Find first body SkinnedMesh as skeleton and bindMatrix source. */
  private bodySkinnedMesh(): THREE.SkinnedMesh | null {
    const skinned: THREE.SkinnedMesh[] = []

    this.root.traverse(child => {
      if (child instanceof THREE.SkinnedMesh) {
        skinned.push(child)
      }
    })

    return skinned[0] ?? null
  }

  /** Lazily build the shared body collision proxy, or return the cached one. */
  private ensureBodyCollider(bodyMesh: THREE.SkinnedMesh): BodyCollider | null {
    if (this.bodyCollider) {
      return this.bodyCollider
    }

    try {
      this.bodyCollider = new BodyCollider(bodyMesh)
    } catch (err) {
      log.warn('character', 'body collision proxy build failed:', err)
      this.bodyCollider = null
    }

    return this.bodyCollider
  }

  /** Collect Mesh / SkinnedMesh leaves from a loaded unit scene; flags shadows. */
  private collectUnitMeshes(scene: THREE.Object3D, skinnedOnly: boolean): THREE.Mesh[] {
    const found: THREE.Mesh[] = []

    scene.traverse(child => {
      if (child instanceof THREE.Mesh && (!skinnedOnly || child instanceof THREE.SkinnedMesh)) {
        child.castShadow = true
        child.receiveShadow = true
        found.push(child)
      }
    })

    return found
  }

  /** Find a bone in the body skeleton by exact or suffix name (mixamorig: tolerant). */
  private findBodyBone(name: string): THREE.Bone | null {
    const skeleton = this.bodySkinnedMesh()?.skeleton

    if (!skeleton) {
      return null
    }

    const exact = skeleton.bones.find(b => b.name === name)

    if (exact) {
      return exact
    }

    const suffix = name.split(':').pop() ?? name

    return skeleton.bones.find(b => (b.name.split(':').pop() ?? b.name) === suffix) ?? null
  }

  /** Rebind garment SkinnedMesh to body skeleton (zero-mapping or bone name remap). */
  private rebindGarmentMesh(
    mesh: THREE.SkinnedMesh,
    bodySkeleton: THREE.Skeleton,
    bodyBindMatrix: THREE.Matrix4 | null,
    bodyBoneNames: string[]
  ): void {
    // Defensive: check if joint names match. If not, remap skinIndices.
    if (!mesh.skeleton) {
      log.warn('character', 'garment mesh has no skeleton — skipping rebind')

      return
    }

    const garmentBoneNames: string[] = mesh.skeleton.bones.map(b => b.name)

    const jointsMatch =
      garmentBoneNames.length === bodyBoneNames.length && garmentBoneNames.every((name, i) => name === bodyBoneNames[i])

    if (!jointsMatch) {
      log.warn('character', 'garment joint order mismatch — remapping by bone name')
      const boneIndexMap = new Map<string, number>()
      bodyBoneNames.forEach((name, i) => boneIndexMap.set(name, i))

      const skinAttr = mesh.geometry.getAttribute('skinIndex')

      if (skinAttr) {
        const indices = skinAttr.array as unknown as number[]

        for (let i = 0; i < indices.length; i++) {
          const garmentBoneIdx = indices[i]
          const boneName = garmentBoneNames[garmentBoneIdx]

          if (boneName !== undefined) {
            indices[i] = boneIndexMap.get(boneName) ?? 0
          }
        }

        skinAttr.needsUpdate = true
      }
    }

    mesh.bind(bodySkeleton, bodyBindMatrix ?? undefined)
    mesh.bindMode = THREE.DetachedBindMode
  }

  /** Dispose an assembled unit and remove from scene. */
  private disposeUnit(unit: AssembledUnit): void {
    for (const p of unit.physics) {
      this.physics.destroyUnit(p)
    }

    if (unit.anchors) {
      for (const anchor of unit.anchors) {
        disposeObjectTree(anchor)
        anchor.parent?.remove(anchor)
      }
    }

    disposeObjectTree(unit.group)
    this.root.remove(unit.group)
  }

  /** Load and bind a PBR channel texture, optionally scoped to target meshes. */
  private loadPbrChannel(
    url: string,
    channel: PbrChannel,
    colorSpace: THREE.ColorSpace,
    slot: PbrSlot,
    targetMeshes?: THREE.Mesh[]
  ): void {
    const epoch = this.textureEpoch
    const desktop = window.spiritagent

    void (async () => {
      // Same host-strip via IPC as the GLB path; data URL is fine for textures
      // (≪ GLB) and ``THREE.TextureLoader`` accepts them.
      let dataUrl: string | null = null

      try {
        dataUrl = await desktop.apiAsset({ url })
      } catch (err) {
        log.warn('character', `PBR channel '${channel}' fetch failed, falling back to native texture:`, err)

        // Fallback: restore native base texture if available
        if (targetMeshes) {
          for (const mesh of targetMeshes) {
            const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material]

            for (const m of mats) {
              if (isPbrMaterial(m) && m.userData) {
                if (slot === 'map' && m.userData.baseMap) {
                  setPbrSlot(m, 'map', m.userData.baseMap)
                }

                if (slot === 'normalMap' && m.userData.baseNormalMap) {
                  setPbrSlot(m, 'normalMap', m.userData.baseNormalMap)
                }
              }
            }
          }
        } else {
          this.root.traverse(child => {
            if (!(child instanceof THREE.Mesh) || this.isUnitDescendant(child)) {
              return
            }

            const mats = Array.isArray(child.material) ? child.material : [child.material]

            for (const m of mats) {
              if (isPbrMaterial(m) && m.userData) {
                if (slot === 'map' && m.userData.baseMap) {
                  setPbrSlot(m, 'map', m.userData.baseMap)
                }

                if (slot === 'normalMap' && m.userData.baseNormalMap) {
                  setPbrSlot(m, 'normalMap', m.userData.baseNormalMap)
                }
              }
            }
          })
        }

        return
      }

      if (epoch < this.textureEpoch || !dataUrl) {
        return
      }

      this.textureLoader.load(dataUrl, tex => {
        // Stale callback: a newer setOutfit / disposeRoot invalidated this load.
        // Dispose the freshly-decoded texture (never bound to a mesh) and bail.
        if (epoch < this.textureEpoch) {
          tex.dispose()

          return
        }

        tex.colorSpace = colorSpace

        if (targetMeshes) {
          for (const mesh of targetMeshes) {
            const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material]

            for (const m of mats) {
              if (isPbrMaterial(m)) {
                setPbrSlot(m, slot, tex)

                if (slot === 'displacementMap') {
                  m.displacementScale = 0.003
                  m.displacementBias = -0.0015
                }
              }
            }
          }

          return
        }

        this.currentPbrTex[channel]?.dispose()
        this.currentPbrTex[channel] = tex

        this.root.traverse(child => {
          if (!(child instanceof THREE.Mesh) || this.isUnitDescendant(child)) {
            return
          }

          const mats = Array.isArray(child.material) ? child.material : [child.material]

          for (const m of mats) {
            if (isPbrMaterial(m)) {
              setPbrSlot(m, slot, tex)

              if (slot === 'displacementMap') {
                m.displacementScale = 0.003
                m.displacementBias = -0.0015
              }
            }
          }
        })
      })
    })()
  }

  /** Dispatch and load PBR channel textures for an outfit item. */
  private bindPbrChannels(item: OutfitItem, targetMeshes?: THREE.Mesh[]): void {
    for (const channel of Object.keys(PBR_CHANNEL_DEFS) as PbrChannel[]) {
      const def = PBR_CHANNEL_DEFS[channel]
      const url = item[def.urlField]

      if (!url) {
        if (!targetMeshes) {
          this.clearPbrChannel(channel, def.slot)
        }

        continue
      }

      this.loadPbrChannel(url, channel, def.colorSpace, def.slot, targetMeshes)
    }
  }

  private clearPbrChannel(channel: PbrChannel, slot: PbrSlot): void {
    const previous = this.currentPbrTex[channel]

    if (previous) {
      this.currentPbrTex[channel] = null
      previous.dispose()
    }

    this.root.traverse(child => {
      if (!(child instanceof THREE.Mesh) || this.isUnitDescendant(child)) {
        return
      }

      const mats = Array.isArray(child.material) ? child.material : [child.material]

      for (const m of mats) {
        if (isPbrMaterial(m)) {
          // Restore native GLB base textures if clearing custom channel
          const fallbackTex =
            slot === 'map'
              ? (m.userData?.baseMap ?? null)
              : slot === 'normalMap'
                ? (m.userData?.baseNormalMap ?? null)
                : slot === 'roughnessMap'
                  ? (m.userData?.baseRoughnessMap ?? null)
                  : slot === 'metalnessMap'
                    ? (m.userData?.baseMetalnessMap ?? null)
                    : null

          setPbrSlot(m, slot, fallbackTex)

          if (slot === 'displacementMap') {
            m.displacementScale = 0
            m.displacementBias = 0
          }
        }
      }
    })
  }

  setLookTarget(nx: number, ny: number): void {
    // nx, ny normalised to [-1, 1] from screen centre
    this.lookX = THREE.MathUtils.clamp(nx, -1, 1)
    this.lookY = THREE.MathUtils.clamp(ny, -1, 1)
  }

  private dragTilt = { x: 0, z: 0 }

  setDragVelocity(vx: number, vy: number): void {
    // vx, vy normalised in px/ms
    this.dragTilt.z = THREE.MathUtils.clamp(-vx * 0.12, -0.25, 0.25)
    this.dragTilt.x = THREE.MathUtils.clamp(vy * 0.08, -0.2, 0.2)
  }

  update(delta: number): void {
    this.breathPhase += delta
    this.mixer?.update(delta)
    this.morph.update(delta)

    // Smoothly decay drag tilt
    this.dragTilt.x = THREE.MathUtils.lerp(this.dragTilt.x, 0, 0.1)
    this.dragTilt.z = THREE.MathUtils.lerp(this.dragTilt.z, 0, 0.1)

    if (this.isProcedural) {
      this.updateProcedural(delta)
    } else {
      // Subtle idle float for GLB characters whose clip may not include it
      this.root.position.y = Math.sin(this.breathPhase * 0.8) * 0.01
    }

    // Cloth units read the skeleton's bone matrices (updated by the renderer
    // for the body SkinnedMesh) — one frame of lag, invisible at 60fps.
    this.bodyCollider?.update()

    for (const unit of this.units) {
      for (const p of unit.physics) {
        p.step(delta)
      }
    }

    this.applyLookAt()
  }

  dispose(): void {
    this.disposeRoot(null)
  }

  private playClip(name: string, fade: number): void {
    const next = this.actions.get(name)

    if (!next) {
      return
    }

    if (next === this.currentAction && next.isRunning()) {
      return
    }

    next.reset().setEffectiveWeight(1).setEffectiveTimeScale(1)
    this.currentAction?.crossFadeTo(next, fade, false)
    next.play()
    this.currentAction = next
  }

  private applyLookAt(): void {
    // Subtle body yaw towards cursor
    const yaw = this.lookX * 0.12
    this.root.rotation.y = THREE.MathUtils.lerp(this.root.rotation.y, yaw, 0.08)

    // Drag physics inertia only during active drag (smoothly decays to 0 at rest)
    const pitch = this.dragTilt.x * 0.4
    const roll = this.dragTilt.z * 0.4
    this.root.rotation.x = THREE.MathUtils.lerp(this.root.rotation.x, pitch, 0.1)
    this.root.rotation.z = THREE.MathUtils.lerp(this.root.rotation.z, roll, 0.1)

    // Subtle chin-tuck posture + cursor gaze tracking for natural, engaged eye-level contact
    // In human portraiture, a slight chin-tuck (~3°) flatters the jawline, engages direct eye contact,
    // and eliminates the detached "looking up at the ceiling" appearance from raw AI rigs.
    if (this.headBone && this.isBipedRig) {
      const chinTuckPitch = 0.05
      const lookPitch = -this.lookY * 0.06
      const lookYaw = this.lookX * 0.1

      _EULER.set(chinTuckPitch + lookPitch, lookYaw, 0, 'YXZ')
      _QUAT.setFromEuler(_EULER)
      this.headBone.quaternion.multiply(_QUAT)

      if (this.neckBone) {
        _EULER.set((chinTuckPitch + lookPitch) * 0.25, lookYaw * 0.25, 0, 'YXZ')
        _QUAT.setFromEuler(_EULER)
        this.neckBone.quaternion.multiply(_QUAT)
      }
    }
  }

  // ── Procedural fallback character ───────────────────────────
  // Scope: the pre-portrait onboarding window (no identity images exist yet)
  // and the absolute last resort when the static-sprite album has no usable
  // image (backend down / all generations rejected). Post-portrait, the
  // degraded renderer is the static sprite layer — never blank (invariant #10).

  private createProcedural(scene: THREE.Scene): void {
    this.isProcedural = true
    const group = new THREE.Group()

    const bodyGeo = new THREE.SphereGeometry(0.5, 48, 48)

    const bodyMat = new THREE.MeshStandardMaterial({
      color: 0xfff4d6,
      emissive: 0x332200,
      emissiveIntensity: 0.15,
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
    const mouthMat = new THREE.MeshStandardMaterial({ color: 0xc89060, roughness: 0.4 })
    const mouth = new THREE.Mesh(mouthGeo, mouthMat)
    mouth.position.set(0, 1.04, 0.4)
    group.add(mouth)

    // Prefabricated crack line decorations on shell surface
    const cracks: THREE.Line[] = []
    const crackMats: THREE.LineBasicMaterial[] = []

    const crackPathsPoints = [
      // Crack 1: Upper left
      [
        new THREE.Vector3(-0.15, 1.3, 0.42),
        new THREE.Vector3(-0.25, 1.22, 0.38),
        new THREE.Vector3(-0.2, 1.12, 0.43),
        new THREE.Vector3(-0.32, 1.05, 0.35)
      ],
      // Crack 2: Mid right
      [
        new THREE.Vector3(0.25, 1.1, 0.4),
        new THREE.Vector3(0.35, 1.0, 0.32),
        new THREE.Vector3(0.28, 0.9, 0.39),
        new THREE.Vector3(0.38, 0.82, 0.28)
      ],
      // Crack 3: Lower left
      [new THREE.Vector3(-0.2, 0.85, 0.42), new THREE.Vector3(-0.28, 0.78, 0.36), new THREE.Vector3(-0.18, 0.7, 0.41)]
    ]

    crackPathsPoints.forEach(pts => {
      const geo = new THREE.BufferGeometry().setFromPoints(pts)

      const mat = new THREE.LineBasicMaterial({
        color: 0xffd166,
        transparent: true,
        opacity: 0.4
      })

      const crackLine = new THREE.Line(geo, mat)
      group.add(crackLine)
      cracks.push(crackLine)
      crackMats.push(mat)
    })

    this.proc = { body, leftEye, rightEye, mouth, cracks, crackMats, group }
    this.root = group
    scene.add(group)
  }

  private updateProcedural(_delta: number): void {
    if (!this.proc) {
      return
    }

    const t = this.breathPhase

    // 3.4s emissive breathing cycle matching login-glow and installer glow
    const reducedMotion =
      typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches

    const breath = reducedMotion ? 0.5 : Math.sin((t * 2 * Math.PI) / 3.4) * 0.5 + 0.5
    const bodyMat = this.proc.body.material as THREE.MeshStandardMaterial

    if (reducedMotion) {
      bodyMat.emissiveIntensity = 0.35
    } else {
      bodyMat.emissiveIntensity = 0.15 + 0.4 * breath

      // 12s periodic crack opacity flash
      if (this.proc.crackMats && this.proc.crackMats.length > 0) {
        const cycle = t % 12
        const activeIdx = Math.floor(t / 12) % this.proc.crackMats.length
        this.proc.crackMats.forEach((mat, idx) => {
          if (idx === activeIdx && cycle < 0.6) {
            const flashWave = Math.sin((cycle / 0.6) * Math.PI)
            mat.opacity = 0.4 + 0.5 * flashWave
          } else {
            mat.opacity = 0.4
          }
        })
      }
    }

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
