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
  // 几何衣橱（见 PROTOCOL.md §1.6 与 companion README §9）。
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

// 衣橱管线可以在 WardrobeItem 上填充的通道。键是 JSON 响应中的 URL 字段名，
// 值是该贴图所绑定的 MeshStandardMaterial 插槽。新增通道
// 意味着要在 ORM、schema 与本表各加一个新字段。
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

// 在 TSL 物理后端下，布料单元的材质会变成 MeshStandardNodeMaterial。
// 两者暴露相同的 PBR 插槽，所以绑定与释放都能兼容。
type PbrMaterial = THREE.MeshStandardMaterial | MeshStandardNodeMaterial

const isPbrMaterial = (m: THREE.Material): m is PbrMaterial =>
  m instanceof THREE.MeshStandardMaterial || m instanceof MeshStandardNodeMaterial

/** 把衣橱通道贴图绑定到 GLB 自带的 PBR 材质上。 */
const applyChannelTexture = (m: THREE.Material, slot: PbrSlot, tex: THREE.Texture | null): void => {
  if (isPbrMaterial(m)) {
    setPbrSlot(m, slot, tex)

    if (slot === 'displacementMap') {
      m.displacementScale = 0.003
      m.displacementBias = -0.0015
    }
  }
}

/** 恢复 GLB 自带的 map / normalMap 基础贴图（拉取失败回退 + 通道清理）。 */
const restoreBaseTexture = (m: THREE.Material, slot: PbrSlot): void => {
  if (slot !== 'map' && slot !== 'normalMap') {
    return
  }

  const userData = m.userData as { baseMap?: THREE.Texture; baseNormalMap?: THREE.Texture } | undefined
  const base = slot === 'map' ? userData?.baseMap : userData?.baseNormalMap

  if (!base) {
    return
  }

  if (isPbrMaterial(m)) {
    setPbrSlot(m, slot, base)
  }
}

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

// 释放 Object3D 层级下的几何、材质与贴图。
const disposeObjectTree = (root: THREE.Object3D): void => {
  root.traverse(child => {
    if (child instanceof THREE.Mesh || child instanceof THREE.Line || child instanceof THREE.LineSegments) {
      child.geometry?.dispose()
      const mats = Array.isArray(child.material) ? child.material : [child.material]

      for (const mat of mats) {
        if (!mat) {
          continue
        }

        // 释放材质前先释放 PBR 贴图——material.dispose() 不会释放 GPU 贴图引用。
        // currentPbrTex 记录由 setOutfit 加载的贴图（由调用方释放）；
        // 本次扫描覆盖仅随材质存活的 GLB 内嵌贴图。dispose() 本身是幂等的。
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
 * 透明地解压经过 Gzip / Deflate 压缩的 GLB 缓冲。
 * 在大幅缩小传输体积的同时，保留 100% 的网格分辨率与精度。
 */
async function decompressGlbIfNeeded(buffer: ArrayBuffer): Promise<ArrayBuffer> {
  const bytes = new Uint8Array(buffer)

  // Gzip 魔数（0x1f, 0x8b）
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

  // Deflate / zlib 魔数（0x78 0x9c / 0x78 0x01 / 0x78 0xda）
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
  private clips = new Map<string, THREE.AnimationClip>()
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
  // 记录最近应用的 PBR 通道贴图，以便热替换时先释放旧资源再装入新资源。
  private currentPbrTex: Record<PbrChannel, THREE.Texture | null> = {
    albedo: null,
    normal: null,
    roughness: null,
    metalness: null,
    displacement: null
  }
  // 单调递增的 epoch——快速连续 setOutfit 时，过期的 textureLoader 回调
  // （如加载完成顺序与发起顺序相反）据此释放自己解码的贴图并退出。
  private textureEpoch = 0
  private readonly textureLoader = new THREE.TextureLoader()

  // 已装配的衣橱单元（PROTOCOL.md §1.6）：每个已装备的
  // 几何项（服装或配件）对应一条记录；贴图项应用于身体根节点，不在此登记。
  private units: AssembledUnit[] = []
  private bodyCollider: BodyCollider | null = null
  private boneRestQuats = new Map<string, THREE.Quaternion>()

  constructor(physics: PhysicsBackend) {
    this.physics = physics
  }

  private getAction(name: string): THREE.AnimationAction | null {
    if (!this.mixer) {
      return null
    }

    let action = this.actions.get(name)

    if (!action) {
      const clip = this.clips.get(name)

      if (clip) {
        action = this.mixer.clipAction(clip)
        this.actions.set(name, action)
      }
    }

    return action ?? null
  }

  /** 解析预取的 GLB 字节与动画；出错时回退到程序化形象。字节来自渲染进程的 `apiAssetBuffer` IPC（主进程已剥离 host 并重写 base，无 CORS 预检）。若提供了 `contentHash` 且 `gltf-instance-cache` 中已有解析好的模板，则取出深克隆而非重新解析。 */
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

        // 分离缓存：深克隆比重解析 GLB 便宜得多。
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

          // 模板由缓存持有；后续取出时返回深克隆。
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

        this.clips.clear()
        this.actions.clear()

        for (const clip of gltfAnimations) {
          this.clips.set(clip.name, clip)
        }

        this.headBone = null
        this.neckBone = null
        this.boneRestQuats.clear()
        this.root.traverse(child => {
          if (child instanceof THREE.Bone) {
            if (child.name.startsWith('mixamorig:')) {
              child.name = child.name.slice(10)
            } else if (child.name.startsWith('mixamorig_')) {
              child.name = child.name.slice(10)
            } else if (child.name.startsWith('mixamorig') && child.name.length > 9) {
              child.name = child.name.slice(9)
            }

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
          this.clips.set(clip.name, clip)
        }

        for (const def of this.injectedClipDefs) {
          const clip = buildClip(def, this.boneRestQuats)
          this.clips.set(clip.name, clip)
        }

        this.actionNames = new Set(this.clips.keys())
        $availableClipNames.set(new Set(this.actionNames))
        this.morph.discover(this.root)
        this.applyState(this.currentState, null)

        return {
          hasMorphTargets: this.morph.hasTargets(),
          hasAnimations: this.clips.size > 0,
          clipNames: [...this.clips.keys()],
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

  private disposeRoot(scene: THREE.Scene | null): void {
    // 先递增 epoch，使进行中的 textureLoader 回调释放其刚解码的贴图并退出。
    this.textureEpoch++

    this.headBone = null
    this.neckBone = null

    if (this.root.parent) {
      scene?.remove(this.root)
    }

    this.mixer?.stopAllAction()
    this.mixer = null
    this.clips.clear()
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

      const clip = buildClip(def, this.boneRestQuats)
      this.clips.set(clip.name, clip)
      this.actionNames.add(clip.name)

      if (this.mixer && this.actions.has(clip.name)) {
        const oldAction = this.actions.get(clip.name)
        oldAction?.stop()
        this.mixer.uncacheClip(clip)
        this.actions.delete(clip.name)
      }
    }

    $availableClipNames.set(new Set(this.actionNames))
  }

  /** 移除指定的动态动作。 */
  removeClip(name: string): void {
    this.injectedClipDefs = this.injectedClipDefs.filter(d => d.name !== name)
    this.clips.delete(name)
    this.actionNames.delete(name)
    const action = this.actions.get(name)

    if (action) {
      action.stop()
      this.mixer?.uncacheClip(action.getClip())
      this.actions.delete(name)
    }

    $availableClipNames.set(new Set(this.actionNames))
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
        // 调用方已解析并校验过该 override（例如 interaction.ts）
        clipName = ctx.clipOverride
      } else if (ctx?.interactionBucket) {
        // 基于标签的交互动作选择
        clipName = resolveInteractionClip(ctx.interactionBucket, tags, library, available)
      } else if (state === 'emotional' && emotion) {
        // 基于标签的情绪动作选择
        clipName = resolveEmotionClip(emotion, tags, library, available, ctx?.customExpressions, ctx?.action)
      }

      // 规范状态→动作映射（MODEL_SPEC §3）；override / 交互 / 情绪动作均无解析时的兜底。
      if (!clipName) {
        clipName = resolveClip(state, this.actionNames)
      }

      if (clipName) {
        this.playClip(clipName, 0.25)
      }
    }
  }

  /** TTS 驱动的嘴型同步振幅 [0..1]。 */
  setLipSyncAmplitude(amp: number): void {
    this.morph.setLipSyncAmplitude(amp)

    if (this.isProcedural && this.proc) {
      this.proc.mouth.scale.y = 1 + amp * 5
    }
  }

  /** 施加身材形变形目标权重（0.0–1.0）。 */
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

  /** 应用已装备的衣橱套装（贴图热替换 + 几何装配）。 */
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
      // 未装备贴图项——显式清理上一套外观残留的身体 PBR 绑定
      // （stub-object 分支会顺带清理，此处保留显式清理以保持意图明确）。
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
        if (!isPbrMaterial(mat) || !mat.color) {
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

  /** 装配一个几何单元（服装或配饰）。 */
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
        // 将锚点挂到 socket 骨骼并做补偿，使网格在继承骨骼运动
        // 的同时，仍保持 rest pose 下原本的世界位置。
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
      // 服装分支：找到身体的 SkinnedMesh 以获取 skeleton 与 bindMatrix。
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
        // 布料与皮肤单元都按普通 Mesh 渲染（无 GPU 蒙皮），
        // 求解器写出的顶点位置即最终位置。TSL 后端通过
        // node-material 的 positionNode 而非 attribute 写入实现这一点。
        // 布料把自由顶点交给 verlet 求解器；皮肤把每个顶点钉在身体上，
        // 防止动画时穿模。
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

    // PBR 贴图绑定仅作用于该单元自身的网格。
    this.bindPbrChannels(item, meshes)
  }

  /** 找到首个身体的 SkinnedMesh，作为 skeleton 与 bindMatrix 的来源。 */
  private bodySkinnedMesh(): THREE.SkinnedMesh | null {
    const skinned: THREE.SkinnedMesh[] = []

    this.root.traverse(child => {
      if (child instanceof THREE.SkinnedMesh) {
        skinned.push(child)
      }
    })

    return skinned[0] ?? null
  }

  /** 按需构建共享的身体碰撞代理，或返回已缓存的实例。 */
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

  /** 从已加载的单元场景中收集 Mesh / SkinnedMesh 叶子节点；标记阴影投射。 */
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

  /** 按精确名或后缀名在身体骨架中查找骨骼（兼容 mixamorig: 前缀）。 */
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

  /** 将服装 SkinnedMesh 重绑到身体骨架上（零映射或按骨骼名重映射）。 */
  private rebindGarmentMesh(
    mesh: THREE.SkinnedMesh,
    bodySkeleton: THREE.Skeleton,
    bodyBindMatrix: THREE.Matrix4 | null,
    bodyBoneNames: string[]
  ): void {
    // 防御性：检查关节名是否对应；不对应则重映射 skinIndices。
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

  /** 释放已装配的单元并从场景中移除。 */
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

  /** 加载并绑定 PBR 通道贴图，可选限定到目标网格。 */
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
      // 与 GLB 路径一样经 IPC 走 host-strip；贴图用 data URL 即可
      // (≪ GLB) and ``THREE.TextureLoader`` accepts them.
      let dataUrl: string | null = null

      try {
        dataUrl = await desktop.apiAsset({ url })
      } catch (err) {
        log.warn('character', `PBR channel '${channel}' fetch failed, falling back to native texture:`, err)

        // 兜底：若有原生基础贴图则恢复
        if (targetMeshes) {
          for (const mesh of targetMeshes) {
            const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material]

            for (const m of mats) {
              restoreBaseTexture(m, slot)
            }
          }
        } else {
          this.root.traverse(child => {
            if (!(child instanceof THREE.Mesh) || this.isUnitDescendant(child)) {
              return
            }

            const mats = Array.isArray(child.material) ? child.material : [child.material]

            for (const m of mats) {
              restoreBaseTexture(m, slot)
            }
          })
        }

        return
      }

      if (epoch < this.textureEpoch || !dataUrl) {
        return
      }

      this.textureLoader.load(dataUrl, tex => {
        // 过期回调：更新的 setOutfit / disposeRoot 已使本加载失效。
        // 释放刚解码的贴图（从未绑定到网格）并退出。
        if (epoch < this.textureEpoch) {
          tex.dispose()

          return
        }

        tex.colorSpace = colorSpace

        if (targetMeshes) {
          for (const mesh of targetMeshes) {
            const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material]

            for (const m of mats) {
              applyChannelTexture(m, slot, tex)
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
            applyChannelTexture(m, slot, tex)
          }
        })
      })
    })()
  }

  /** 为装扮项分发并加载 PBR 通道贴图。 */
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
          // 清理自定义通道时恢复 GLB 原生基础贴图
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
    // nx, ny 从屏幕中心归一化到 [-1, 1]
    this.lookX = THREE.MathUtils.clamp(nx, -1, 1)
    this.lookY = THREE.MathUtils.clamp(ny, -1, 1)
  }

  private dragTilt = { x: 0, z: 0 }

  setDragVelocity(vx: number, vy: number): void {
    // vx, vy 以 px/ms 为单位归一化
    this.dragTilt.z = THREE.MathUtils.clamp(-vx * 0.12, -0.25, 0.25)
    this.dragTilt.x = THREE.MathUtils.clamp(vy * 0.08, -0.2, 0.2)
  }

  update(delta: number): void {
    this.breathPhase += delta
    this.mixer?.update(delta)
    this.morph.update(delta)

    // 平滑衰减拖拽倾角
    this.dragTilt.x = THREE.MathUtils.lerp(this.dragTilt.x, 0, 0.1)
    this.dragTilt.z = THREE.MathUtils.lerp(this.dragTilt.z, 0, 0.1)

    if (this.isProcedural) {
      this.updateProcedural(delta)
    } else {
      // GLB 角色若内置动作不包含 idle 浮动，则手动添加细微的 idle 浮动
      this.root.position.y = Math.sin(this.breathPhase * 0.8) * 0.01
    }

    // 布料单元读取骨骼矩阵（由渲染器按身体的 SkinnedMesh 更新）——
    // 有一帧延迟，60fps 下不可见。
    // 仅当存在活跃的物理单元需要参与碰撞时，才更新身体碰撞体与 BVH。
    const hasActivePhysics = this.units.some(unit => unit.physics.length > 0)

    if (hasActivePhysics) {
      this.bodyCollider?.update()

      for (const unit of this.units) {
        for (const p of unit.physics) {
          p.step(delta)
        }
      }
    }

    this.applyLookAt()
  }

  dispose(): void {
    this.disposeRoot(null)
  }

  private playClip(name: string, fade: number): void {
    const next = this.getAction(name)

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
    // 身体向光标方向微微偏航
    const yaw = this.lookX * 0.12
    this.root.rotation.y = THREE.MathUtils.lerp(this.root.rotation.y, yaw, 0.08)

    // 仅在主动拖拽期间保留拖拽惯性（静止时平滑衰减为 0）
    const pitch = this.dragTilt.x * 0.4
    const roll = this.dragTilt.z * 0.4
    this.root.rotation.x = THREE.MathUtils.lerp(this.root.rotation.x, pitch, 0.1)
    this.root.rotation.z = THREE.MathUtils.lerp(this.root.rotation.z, roll, 0.1)

    // 微微下颌内收 + 光标视线追踪，营造自然、有交流感的平视眼神。
    // 人像摄影里约 3° 的下颌内收能让下颌线更柔和、视线更聚焦，
    // 避免 AI 原始骨骼那种"仰头看天花板"的脱节感。
    if (this.headBone && this.isBipedRig) {
      const restHead = this.boneRestQuats.get(this.headBone.name)
      const chinTuckPitch = 0.05
      const lookPitch = -this.lookY * 0.06
      const lookYaw = this.lookX * 0.1

      _EULER.set(chinTuckPitch + lookPitch, lookYaw, 0, 'YXZ')
      _QUAT.setFromEuler(_EULER)

      if (restHead) {
        this.headBone.quaternion.copy(restHead).multiply(_QUAT)
      } else {
        this.headBone.quaternion.multiply(_QUAT)
      }

      if (this.neckBone) {
        const restNeck = this.boneRestQuats.get(this.neckBone.name)
        _EULER.set((chinTuckPitch + lookPitch) * 0.25, lookYaw * 0.25, 0, 'YXZ')
        _QUAT.setFromEuler(_EULER)

        if (restNeck) {
          this.neckBone.quaternion.copy(restNeck).multiply(_QUAT)
        } else {
          this.neckBone.quaternion.multiply(_QUAT)
        }
      }
    }
  }

  // ── Procedural fallback character ───────────────────────────
  // 适用场景：半身像生成前的 onboarding 窗口（还没有形象图），
  // 以及静态相册彻底无可用图时的最后兜底
  // （后端宕机 / 所有生成都被拒）。半身像就绪后，
  // 降级渲染层切到静态精灵——画面永不空白（不变量 #10）。

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

    // 蛋壳表面的预制裂纹线装饰
    const cracks: THREE.Line[] = []
    const crackMats: THREE.LineBasicMaterial[] = []

    const crackPathsPoints = [
      // 裂纹 1：左上
      [
        new THREE.Vector3(-0.15, 1.3, 0.42),
        new THREE.Vector3(-0.25, 1.22, 0.38),
        new THREE.Vector3(-0.2, 1.12, 0.43),
        new THREE.Vector3(-0.32, 1.05, 0.35)
      ],
      // 裂纹 2：中右
      [
        new THREE.Vector3(0.25, 1.1, 0.4),
        new THREE.Vector3(0.35, 1.0, 0.32),
        new THREE.Vector3(0.28, 0.9, 0.39),
        new THREE.Vector3(0.38, 0.82, 0.28)
      ],
      // 裂纹 3：左下
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

    // 每帧重置变换矩阵
    this.proc.group.position.y = 0
    this.proc.body.scale.set(0.82, 1.08, 0.82)
    this.proc.body.rotation.z = 0
    this.proc.mouth.scale.y = 1

    switch (this.currentState) {
      case 'speaking': {
        this.proc.group.position.y = Math.sin(t * 5) * 0.015

        // 嘴型缩放由 setLipSyncAmplitude 驱动，而非正弦波
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

    // 程序化眨眼——上面 sleeping 已提前返回
    const blinkCycle = t % (3 + (this.currentState.charCodeAt(0) % 3))
    const blinkWindow = blinkCycle > 2.8 && blinkCycle < 2.95
    const eyeScaleY = blinkWindow ? 0.1 : 1
    this.proc.leftEye.scale.y = eyeScaleY
    this.proc.rightEye.scale.y = eyeScaleY
  }
}
