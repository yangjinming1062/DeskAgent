/** Mesh2D SkinnedMesh 渲染运行时 — 加载 manifest + 部件 PNG，构造 SkinnedMesh 场景。
 *
 * 设计要点（DESIGN.md §2.3 + mesh2d README）：
 * - tickMesh2D 拆四层叠加：base pose（drivers 写入）→ 持续微动（breath/blink/sway）→ 视线跟随 → jiggle；
 * - drivers 负责 action / locomotion / idle variant，本模块只关心持续微动 + 视线 + 嘴型 + jiggle 物理。
 */

import * as THREE from 'three'

import { log } from '@/shared/lib/log'

import { clampJiggleOffset, createJiggleState, type JiggleConfig, type JiggleState, stepJiggle } from './mesh2d-bones'

export interface ManifestBone {
  name: string
  pivot: [number, number]
  parent: string | null
  z_order: number
}

export interface ManifestMesh {
  name: string
  texture: string
  geometry_w: number
  geometry_h: number
  z_order: number
  origin?: [number, number]
  bones_influences: { bone: string; weight: number }[]
}

export interface Manifest {
  version?: number
  canvas: { w: number; h: number }
  skeleton: { bones: ManifestBone[] }
  meshes: ManifestMesh[]
  animations: {
    breath: { amplitude: number; period_ms: number }
    blink: { min_period_ms: number; max_period_ms: number; duration_ms: number }
    idle_sway: { amplitude: number; min_period_ms: number; max_period_ms: number }
    jiggle: Record<string, JiggleConfig>
    // 骨骼 transform 红线（来自 backend/services/companion/mesh2d/manifest_exporter.py::_BONE_RED_LINES）。
    // 驱动层 clampBoneTransform 必须使用这里的数据，不硬编码。
    red_lines?: Record<string, { rot_max: number; scale_y_max?: number }>
    actions?: Record<string, unknown>
    idle_variants?: string[]
    locomotion?: Record<string, unknown>
  }
}

export interface Mesh2DScene {
  group: THREE.Group
  skeleton: THREE.Skeleton
  bones: Map<string, THREE.Bone>
  meshes: THREE.SkinnedMesh[]
  width: number
  height: number
  textures: THREE.Texture[]
  manifests: Manifest
  jiggleStates: Map<string, JiggleState>
  dispose(): void
}

const TEXTURE_LOADER = new THREE.TextureLoader()
TEXTURE_LOADER.setCrossOrigin('anonymous')

// jiggle 配置名 → 骨骼名：manifest 的 jiggle key 用 "<part>_root" 命名，
// Skeleton 里骨骼通常是去后缀的简写；显式映射，未匹配的退回 key 原值。
const JIGGLE_BONE_MAP: Record<string, string> = {
  hair_back_root: 'back_hair',
  skirt_root: 'skirt'
}

async function loadTexture(url: string): Promise<THREE.Texture> {
  return new Promise((resolve, reject) => {
    TEXTURE_LOADER.load(
      url,
      tex => {
        tex.premultiplyAlpha = false
        tex.minFilter = THREE.LinearFilter
        tex.magFilter = THREE.LinearFilter
        resolve(tex)
      },
      undefined,
      err => reject(err)
    )
  })
}

function buildBonePivot(bone: ManifestBone): THREE.Vector3 {
  // 画布坐标系 Y 向下；Three.js 习惯 Y 向上——这里简单翻转，渲染时相机也按同样规则。
  return new THREE.Vector3(bone.pivot[0], -bone.pivot[1], bone.z_order * 0.001)
}

function createBones(manifest: Manifest): { bones: Map<string, THREE.Bone>; root: THREE.Bone; list: THREE.Bone[] } {
  const bonesByName = new Map<string, THREE.Bone>()
  const absPivots = new Map<string, THREE.Vector3>()
  const list: THREE.Bone[] = []

  for (const def of manifest.skeleton.bones) {
    const bone = new THREE.Bone()
    const absPos = buildBonePivot(def)
    absPivots.set(def.name, absPos)
    bonesByName.set(def.name, bone)
    list.push(bone)
  }

  for (const def of manifest.skeleton.bones) {
    const bone = bonesByName.get(def.name)!
    const absPos = absPivots.get(def.name)!

    if (def.parent && bonesByName.has(def.parent)) {
      const parentBone = bonesByName.get(def.parent)!
      const parentAbsPos = absPivots.get(def.parent)!
      bone.position.copy(absPos.clone().sub(parentAbsPos))
      parentBone.add(bone)
    } else {
      bone.position.copy(absPos)
    }
  }

  const root = bonesByName.get('root') ?? list[0]

  return { bones: bonesByName, root, list }
}

function buildSkinnedMesh(
  meshDef: ManifestMesh,
  bones: Map<string, THREE.Bone>,
  texture: THREE.Texture,
  layerSize: { w: number; h: number }
): THREE.SkinnedMesh {
  const segs = 8
  const geomW = meshDef.geometry_w || layerSize.w
  const geomH = meshDef.geometry_h || layerSize.h
  const geometry = new THREE.PlaneGeometry(geomW, geomH, segs, segs)
  const cx = meshDef.origin ? meshDef.origin[0] : geomW / 2
  const cy = meshDef.origin ? -meshDef.origin[1] : -geomH / 2
  geometry.translate(cx, cy, meshDef.z_order * 0.001)

  const material = new THREE.MeshBasicMaterial({
    map: texture,
    transparent: true,
    depthWrite: false,
    depthTest: true,
    alphaTest: 0.01,
    side: THREE.DoubleSide,
    toneMapped: false
  })

  const vertexCount = geometry.attributes.position.count
  const indices = new Uint16Array(vertexCount * 4)
  const weights = new Float32Array(vertexCount * 4)

  for (let v = 0; v < vertexCount; v++) {
    const inf = meshDef.bones_influences[0]

    if (!inf) {
      indices[v * 4 + 0] = 0
      weights[v * 4 + 0] = 1

      continue
    }

    const boneIndex = [...bones.keys()].indexOf(inf.bone)
    indices[v * 4 + 0] = Math.max(0, boneIndex)
    weights[v * 4 + 0] = inf.weight

    for (let k = 1; k < 4; k++) {
      indices[v * 4 + k] = 0
      weights[v * 4 + k] = 0
    }
  }

  geometry.setAttribute('skinIndex', new THREE.Uint16BufferAttribute(indices, 4))
  geometry.setAttribute('skinWeight', new THREE.Float32BufferAttribute(weights, 4))

  const skinned = new THREE.SkinnedMesh(geometry, material)
  skinned.renderOrder = meshDef.z_order
  skinned.frustumCulled = false

  return skinned
}

export async function buildMesh2DScene(manifest: Manifest, layerUrls: Record<string, string>): Promise<Mesh2DScene> {
  const group = new THREE.Group()
  const { bones, root, list } = createBones(manifest)
  const skeleton = new THREE.Skeleton(list)

  group.add(root)

  const textures: THREE.Texture[] = []
  const meshes: THREE.SkinnedMesh[] = []

  for (const meshDef of [...manifest.meshes].sort((a, b) => a.z_order - b.z_order)) {
    const url = layerUrls[meshDef.texture] ?? layerUrls[meshDef.texture.replace('.png', '')]

    if (!url) {
      log.warn('mesh2d-runtime', `layer URL not found for ${meshDef.texture}`)

      continue
    }

    try {
      const texture = await loadTexture(url)
      textures.push(texture)

      const img = texture.image as HTMLImageElement | undefined
      const layerSize = { w: img?.width ?? meshDef.geometry_w, h: img?.height ?? meshDef.geometry_h }
      const skinned = buildSkinnedMesh(meshDef, bones, texture, layerSize)
      skinned.add(root)
      skinned.bind(skeleton)
      group.add(skinned)
      meshes.push(skinned)
    } catch (err) {
      log.warn('mesh2d-runtime', `failed to load texture ${meshDef.texture}`, err)
    }
  }

  const jiggleStates = new Map<string, JiggleState>()

  for (const name of Object.keys(manifest.animations.jiggle)) {
    jiggleStates.set(name, createJiggleState())
  }

  return {
    group,
    skeleton,
    bones,
    meshes,
    width: manifest.canvas.w,
    height: manifest.canvas.h,
    textures,
    manifests: manifest,
    jiggleStates,
    dispose() {
      for (const mesh of meshes) {
        mesh.geometry.dispose()

        if (Array.isArray(mesh.material)) {
          mesh.material.forEach(m => m.dispose())
        } else {
          mesh.material.dispose()
        }
      }

      for (const tex of textures) {
        tex.dispose()
      }
    }
  }
}

export interface FrameInputs {
  dt: number
  elapsed: number
  audioAmp: number
  lookX: number
  lookY: number
  breathActive: boolean
  blinkActive: boolean
  reducedMotion: boolean
  eyeSquint?: boolean
}

export function tickMesh2D(scene: Mesh2DScene, inputs: FrameInputs): void {
  const { manifests, bones, jiggleStates } = scene
  const { breath, idle_sway, jiggle } = manifests.animations
  const dt = Math.min(inputs.dt, 1 / 30)

  // 注意：本函数调用前 Mesh2DDriver.tick() 已写入 base pose（active action + locomotion）。
  // 这里的微动层 / 视线跟随 / 嘴型 / jiggle 都在 base 之上叠加（不直接覆盖），
  // 最后由 driver 的 clampBoneTransform 兜底（已写入到 base 阶段），这里再次 clamp 头/躯干红线。

  // breath：scale.y ∈ [1.0, 1.015]（严守红线），与 base scale.y 相乘而非覆盖。
  // base pose 通常让 scale.y = 1（除 shy/idle_sway_more 等少数动作），所以相乘是安全的。
  if (inputs.breathActive) {
    const wave = Math.sin((inputs.elapsed * Math.PI * 2) / breath.period_ms)
    const breathFactor = 1 + wave * breath.amplitude // ∈ [1.0, 1.015]

    for (const targetName of ['body_main', 'head']) {
      const bone = bones.get(targetName)

      if (bone) {
        // 先记 base，叠加 breath，再 clamp ≤ 1.015
        const baseScaleY = bone.userData.baseScaleY ?? 1
        bone.scale.y = baseScaleY * breathFactor

        if (bone.scale.y > 1.015) {
          bone.scale.y = 1.015
        }
      }
    }
  }

  // idle_sway：head.rotation.z 在 base 之上叠加 ±0.04 rad（约 ±2.3°）。
  // head_turn：head.rotation.{y,x} 在 base 之上叠加 lookX/Y 的视线跟随。
  // 红线：head 任何一轴都不超 ±0.26 rad；最后 clamp 兜底。
  const head = bones.get('head')

  if (head) {
    const baseRot = head.userData.baseRot ?? { x: 0, y: 0, z: 0 }
    const sway = Math.sin((inputs.elapsed * Math.PI * 2) / 6000) * idle_sway.amplitude
    head.rotation.z = baseRot.z + sway
    head.rotation.y = baseRot.y + inputs.lookX * 0.26
    head.rotation.x = baseRot.x - inputs.lookY * 0.15

    // 兜底 clamp（与 driver 的 BONE_RED_LINES 一致）
    const HEAD_MAX = 0.26
    head.rotation.x = Math.max(-HEAD_MAX, Math.min(HEAD_MAX, head.rotation.x))
    head.rotation.y = Math.max(-HEAD_MAX, Math.min(HEAD_MAX, head.rotation.y))
    head.rotation.z = Math.max(-HEAD_MAX, Math.min(HEAD_MAX, head.rotation.z))
  }

  // blink / eyeSquint：eye_L/R.scale.y = 1 → 0.05 → 1（120ms ease）；petting 时眯眼享受（scale.y = 0.15）
  if (inputs.eyeSquint) {
    for (const name of ['eye_L', 'eye_R']) {
      const bone = bones.get(name)

      if (bone) {
        bone.scale.y = 0.15
      }
    }
  } else if (inputs.blinkActive) {
    const blinkPeriod = manifests.animations.blink?.min_period_ms ?? 4000
    const blinkDuration = manifests.animations.blink?.duration_ms ?? 120
    const cycle = inputs.elapsed % blinkPeriod

    const eyeScaleY =
      cycle < blinkDuration
        ? cycle < blinkDuration / 2
          ? 1.0 - 0.95 * (cycle / (blinkDuration / 2))
          : 0.05 + 0.95 * ((cycle - blinkDuration / 2) / (blinkDuration / 2))
        : 1.0

    for (const name of ['eye_L', 'eye_R']) {
      const bone = bones.get(name)

      if (bone) {
        bone.scale.y = eyeScaleY
      }
    }
  } else {
    for (const name of ['eye_L', 'eye_R']) {
      const bone = bones.get(name)

      if (bone) {
        bone.scale.y = 1
      }
    }
  }

  // mouth_open：mouth.scale.y = 1 + amp·0.4；scale.x = 1 - amp·0.1。直接覆盖（mouth 不参与 action）。
  const mouth = bones.get('mouth')

  if (mouth) {
    const amp = Math.min(1, Math.max(0, inputs.audioAmp))
    mouth.scale.y = 1 + amp * 0.4
    mouth.scale.x = 1 - amp * 0.1
  }

  // jiggle 弹簧物理（与 action 解耦——impulse 由 hitmap / 走路 / 物理驱动）
  for (const [name, cfg] of Object.entries(jiggle)) {
    const state = jiggleStates.get(name)

    if (!state || inputs.reducedMotion) {
      continue
    }

    const next = stepJiggle(state, cfg, dt)
    next.offset = clampJiggleOffset(next.offset, 5)
    jiggleStates.set(name, next)

    const bone = bones.get(JIGGLE_BONE_MAP[name] ?? name)
    // basePos 由 driver.cacheBasePose 每帧在写完 base 后快照——position 动作通道上线后，
    // 旧的惰性缓存（首帧 position）必然陈旧。
    const basePos = bone?.userData.basePos as { x: number } | undefined

    if (bone && basePos) {
      bone.position.x = basePos.x + next.offset
    }
  }
}
