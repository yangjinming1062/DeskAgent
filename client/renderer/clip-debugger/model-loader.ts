import * as THREE from 'three'

import { createGLTFLoader } from '@/companion/3d/gltf-loader-factory'
import type { RigType } from '@/companion/3d/rig'

import type { ClipItem, ModelStats, MorphTargetInfo } from './types'

export interface ParsedCharacter {
  root: THREE.Group
  bones: THREE.Bone[]
  boneRestQuats: Map<string, THREE.Quaternion>
  embeddedClips: ClipItem[]
  morphTargets: MorphTargetInfo[]
  stats: ModelStats
}

/**
 * 规范化骨骼名称（移除 mixamorig 前缀等）
 */
export function normalizeBoneName(name: string): string {
  if (name.startsWith('mixamorig:')) {
    return name.slice(10)
  }

  if (name.startsWith('mixamorig_')) {
    return name.slice(10)
  }

  if (name.startsWith('mixamorig') && name.length > 9) {
    return name.slice(9)
  }

  return name
}

/**
 * 收集场景中的统计信息、骨骼、表情 Morph
 */
export function inspectScene(
  root: THREE.Group,
  sourceType: ModelStats['sourceType'],
  name: string,
  byteSize?: number,
  embeddedAnimations: THREE.AnimationClip[] = []
): {
  bones: THREE.Bone[]
  boneRestQuats: Map<string, THREE.Quaternion>
  embeddedClips: ClipItem[]
  morphTargets: MorphTargetInfo[]
  stats: ModelStats
} {
  const bones: THREE.Bone[] = []
  const boneRestQuats = new Map<string, THREE.Quaternion>()
  const morphTargets: MorphTargetInfo[] = []
  let vertexCount = 0
  let triangleCount = 0
  let meshCount = 0

  root.traverse(child => {
    if (child instanceof THREE.Bone) {
      child.name = normalizeBoneName(child.name)
      bones.push(child)
      boneRestQuats.set(child.name, child.quaternion.clone())
    } else if (child instanceof THREE.Mesh) {
      meshCount++

      if (child.geometry) {
        const geo = child.geometry
        const pos = geo.getAttribute('position')

        if (pos) {
          vertexCount += pos.count
        }

        if (geo.index) {
          triangleCount += geo.index.count / 3
        } else if (pos) {
          triangleCount += pos.count / 3
        }
      }

      // Morph targets 发现
      if (child.morphTargetDictionary && child.morphTargetInfluences) {
        for (const [mName, mIndex] of Object.entries(child.morphTargetDictionary)) {
          morphTargets.push({
            name: mName,
            index: mIndex,
            meshName: child.name || `Mesh_${meshCount}`,
            currentValue: child.morphTargetInfluences[mIndex] ?? 0
          })
        }
      }
    }
  })

  const embeddedClips: ClipItem[] = embeddedAnimations.map((anim, idx) => ({
    id: `embedded:${anim.name || `clip_${idx}`}`,
    name: anim.name || `Embedded_${idx + 1}`,
    duration: anim.duration,
    loop: true,
    category: anim.name?.startsWith('preset:') ? 'preset' : 'embedded',
    trackCount: anim.tracks.length,
    animationClip: anim
  }))

  const stats: ModelStats = {
    sourceType,
    name,
    fileSizeBytes: byteSize,
    vertexCount,
    triangleCount: Math.round(triangleCount),
    meshCount,
    boneCount: bones.length,
    hasMorphs: morphTargets.length > 0,
    hasEmbeddedAnimations: embeddedClips.length > 0
  }

  return { bones, boneRestQuats, embeddedClips, morphTargets, stats }
}

/**
 * 自动解压 Gzip / Deflate 压缩的 GLB 二进制数据（后端资产服务默认经过 Gzip 压缩存储与传输）
 */
export async function decompressGlbIfNeeded(buffer: ArrayBuffer): Promise<ArrayBuffer> {
  const bytes = new Uint8Array(buffer)

  // Gzip 魔数（0x1f, 0x8b）
  if (bytes.length >= 2 && bytes[0] === 0x1f && bytes[1] === 0x8b) {
    if (typeof DecompressionStream !== 'undefined') {
      try {
        const ds = new DecompressionStream('gzip')
        const decompressed = await new Response(new Response(bytes).body?.pipeThrough(ds)).arrayBuffer()

        return decompressed
      } catch (err) {
        console.warn('[model-loader] Failed to decompress gzip glb buffer:', err)
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
        console.warn('[model-loader] Failed to decompress deflate glb buffer:', err)
      }
    }
  }

  return buffer
}

/**
 * 解析任意 GLB / GLTF ArrayBuffer 并生成 ParsedCharacter
 */
export async function parseGlbBuffer(rawBuffer: ArrayBuffer, name = 'custom.glb'): Promise<ParsedCharacter> {
  const buffer = await decompressGlbIfNeeded(rawBuffer)
  const loader = createGLTFLoader()
  const gltf = await loader.parseAsync(buffer, '')
  const root = gltf.scene

  // 开启阴影与材质设置
  root.traverse(child => {
    if (child instanceof THREE.Mesh) {
      child.castShadow = true
      child.receiveShadow = true
    }
  })

  const { bones, boneRestQuats, embeddedClips, morphTargets, stats } = inspectScene(
    root,
    'custom-glb',
    name,
    buffer.byteLength,
    gltf.animations
  )

  return { root, bones, boneRestQuats, embeddedClips, morphTargets, stats }
}

/**
 * 读取本地文件为 ArrayBuffer
 */
export async function readGlbFile(file: File): Promise<{ buffer: ArrayBuffer; name: string }> {
  const buffer = await file.arrayBuffer()

  return { buffer, name: file.name }
}

/**
 * 从远程/后端直接获取 GLB 文件 ArrayBuffer
 */
export async function fetchGlbFromUrl(url: string, token?: string): Promise<{ buffer: ArrayBuffer; name: string }> {
  const headers: HeadersInit = {}

  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }

  const res = await fetch(url, { headers })

  if (!res.ok) {
    throw new Error(`获取模型失败: HTTP ${res.status} ${res.statusText}`)
  }

  const buffer = await res.arrayBuffer()
  const name = url.split('/').pop()?.split('?')[0] || 'remote_model.glb'

  return { buffer, name }
}

/**
 * 解析 Base64URL 激活码得到其中的 baseUrl 与 token
 */
export function decodeActivationCode(code: string): { baseUrl: string; token: string } {
  try {
    const clean = code.trim().replace(/-/g, '+').replace(/_/g, '/')
    const padded = clean.padEnd(clean.length + ((4 - (clean.length % 4)) % 4), '=')
    const raw = atob(padded)
    const json = JSON.parse(raw)

    if (!json.b || !json.t) {
      throw new Error('激活码格式不完整：缺少必要字段')
    }

    return { baseUrl: json.b, token: json.t }
  } catch (err: any) {
    throw new Error(`激活码解析失败: ${err?.message || '格式无效'}`)
  }
}

/**
 * 从后端服务 /api/companion/model 获取当前伴侣 GLB 模型
 */
export async function fetchBackendCompanionModel(
  backendUrl = 'http://127.0.0.1:8000',
  token?: string
): Promise<{ buffer: ArrayBuffer; name: string; info: any }> {
  const base = backendUrl.replace(/\/+$/, '')
  const headers: HeadersInit = {}

  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }

  const modelApiUrl = `${base}/api/companion/model`
  const res = await fetch(modelApiUrl, { headers })

  if (!res.ok) {
    throw new Error(`获取后端伴侣信息失败 (${modelApiUrl}): HTTP ${res.status} ${res.statusText}`)
  }

  const json = await res.json()
  const assetUrl = json?.asset_url

  if (!assetUrl) {
    throw new Error('后端返回成功，但该账号尚未生成或绑定 3D 模型 (asset_url 为空)')
  }

  const fullAssetUrl = assetUrl.startsWith('http') ? assetUrl : `${base}${assetUrl}`
  const { buffer, name } = await fetchGlbFromUrl(fullAssetUrl, token)

  return { buffer, name: `${json.species || 'companion'}_${name}`, info: json }
}

/**
 * 使用激活码 (Activation Code) 自动兑换 Session JWT 并拉取后端伴侣 3D 模型
 */
export async function fetchBackendCompanionModelWithActivationCode(
  activationCode: string,
  overrideBackendUrl?: string
): Promise<{ buffer: ArrayBuffer; name: string; info: any }> {
  let resolvedHost = overrideBackendUrl?.trim()

  if (!resolvedHost) {
    const decoded = decodeActivationCode(activationCode)
    resolvedHost = decoded.baseUrl
  }

  const base = resolvedHost.replace(/\/+$/, '')

  // 1. 调用 /api/user/activate 兑换 session token
  const activateUrl = `${base}/api/user/activate`
  let activateRes: Response

  try {
    activateRes = await fetch(activateUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code: activationCode.trim() })
    })
  } catch (err: any) {
    throw new Error(`连接后端服务失败 (${activateUrl}): ${err?.message || err}`)
  }

  if (!activateRes.ok) {
    const errJson = await activateRes.json().catch(() => null)
    const detail = errJson?.detail || activateRes.statusText

    throw new Error(`激活失败 (HTTP ${activateRes.status}): ${detail}`)
  }

  const tokenData = await activateRes.json()
  const accessToken = tokenData.access_token

  if (!accessToken) {
    throw new Error('激活成功但后端未返回有效 access_token')
  }

  // 2. 携带 token 获取当前伴侣 3D 模型
  return await fetchBackendCompanionModel(base, accessToken)
}

/**
 * 创建内置的高精度三维人体/骨骼人偶（Mannequin），自带标准骨骼与面部几何
 */
export function createProceduralMannequin(rigType: RigType): ParsedCharacter {
  const group = new THREE.Group()
  group.name = `Procedural_${rigType}`

  const materialSkin = new THREE.MeshStandardMaterial({
    color: 0x94a3b8,
    roughness: 0.45,
    metalness: 0.1
  })

  const materialJoint = new THREE.MeshStandardMaterial({
    color: 0x38bdf8,
    roughness: 0.2,
    metalness: 0.5,
    emissive: 0x0284c7,
    emissiveIntensity: 0.2
  })

  const materialFace = new THREE.MeshStandardMaterial({
    color: 0x0f172a,
    roughness: 0.3
  })

  const bones: THREE.Bone[] = []

  function makeBone(name: string, yPos = 0, xPos = 0, zPos = 0): THREE.Bone {
    const b = new THREE.Bone()
    b.name = name
    b.position.set(xPos, yPos, zPos)
    bones.push(b)

    return b
  }

  function attachCapsule(
    parent: THREE.Object3D,
    radius: number,
    length: number,
    mat: THREE.Material,
    offset: number | { x?: number; y?: number; z?: number } = 0,
    rot: number | { x?: number; y?: number; z?: number } = 0
  ): THREE.Mesh {
    const geo = new THREE.CapsuleGeometry(radius, Math.max(0.01, length - radius * 2), 12, 12)
    const mesh = new THREE.Mesh(geo, mat)

    if (typeof offset === 'number') {
      mesh.position.y = offset
    } else {
      mesh.position.set(offset.x ?? 0, offset.y ?? 0, offset.z ?? 0)
    }

    if (typeof rot === 'number') {
      mesh.rotation.z = rot
    } else {
      mesh.rotation.set(rot.x ?? 0, rot.y ?? 0, rot.z ?? 0)
    }

    mesh.castShadow = true
    mesh.receiveShadow = true
    parent.add(mesh)

    return mesh
  }

  function attachJoint(parent: THREE.Object3D, radius = 0.05): THREE.Mesh {
    const geo = new THREE.SphereGeometry(radius, 16, 16)
    const mesh = new THREE.Mesh(geo, materialJoint)
    mesh.castShadow = true
    parent.add(mesh)

    return mesh
  }

  if (rigType === 'biped') {
    // ── 双足人形骨骼 (标准 Mixamo / Tripo T-pose Bind Pose) ──
    const hips = makeBone('Hips', 1.0)
    group.add(hips)
    attachCapsule(hips, 0.12, 0.16, materialSkin)
    attachJoint(hips, 0.08)

    const spine = makeBone('Spine', 0.16)
    hips.add(spine)
    attachCapsule(spine, 0.13, 0.18, materialSkin, { x: 0, y: 0.09, z: 0 })

    const spine1 = makeBone('Spine1', 0.18)
    spine.add(spine1)
    attachCapsule(spine1, 0.14, 0.22, materialSkin, { x: 0, y: 0.11, z: 0 })

    const neck = makeBone('Neck', 0.22)
    spine1.add(neck)
    attachCapsule(neck, 0.05, 0.08, materialSkin, { x: 0, y: 0.04, z: 0 })

    const head = makeBone('Head', 0.08)
    neck.add(head)
    attachCapsule(head, 0.12, 0.18, materialSkin, { x: 0, y: 0.12, z: 0 })

    // 面部五官装饰
    const eyeL = new THREE.Mesh(new THREE.SphereGeometry(0.025, 12, 12), materialFace)
    eyeL.position.set(-0.045, 0.14, 0.1)
    head.add(eyeL)

    const eyeR = new THREE.Mesh(new THREE.SphereGeometry(0.025, 12, 12), materialFace)
    eyeR.position.set(0.045, 0.14, 0.1)
    head.add(eyeR)

    const visor = new THREE.Mesh(new THREE.BoxGeometry(0.14, 0.03, 0.04), materialJoint)
    visor.position.set(0, 0.14, 0.09)
    head.add(visor)

    // 左手臂 (标准 T-pose: 沿 -X 方向向外伸展，idle 动作应用 -1.24rad 旋转自然下垂到躯干旁)
    const leftArm = makeBone('LeftArm', 0.18, -0.18, 0)
    spine1.add(leftArm)
    attachJoint(leftArm, 0.045)
    attachCapsule(leftArm, 0.045, 0.22, materialSkin, { x: -0.11, y: 0, z: 0 }, { x: 0, y: 0, z: Math.PI / 2 })

    const leftForeArm = makeBone('LeftForeArm', 0, -0.22, 0)
    leftArm.add(leftForeArm)
    attachJoint(leftForeArm, 0.04)
    attachCapsule(leftForeArm, 0.038, 0.2, materialSkin, { x: -0.1, y: 0, z: 0 }, { x: 0, y: 0, z: Math.PI / 2 })

    const leftHand = makeBone('LeftHand', 0, -0.2, 0)
    leftForeArm.add(leftHand)
    attachCapsule(leftHand, 0.035, 0.08, materialJoint, { x: -0.04, y: 0, z: 0 }, { x: 0, y: 0, z: Math.PI / 2 })

    // 右手臂 (标准 T-pose: 沿 +X 方向向外伸展，idle 动作应用 +1.24rad 旋转自然下垂到躯干旁)
    const rightArm = makeBone('RightArm', 0.18, 0.18, 0)
    spine1.add(rightArm)
    attachJoint(rightArm, 0.045)
    attachCapsule(rightArm, 0.045, 0.22, materialSkin, { x: 0.11, y: 0, z: 0 }, { x: 0, y: 0, z: -Math.PI / 2 })

    const rightForeArm = makeBone('RightForeArm', 0, 0.22, 0)
    rightArm.add(rightForeArm)
    attachJoint(rightForeArm, 0.04)
    attachCapsule(rightForeArm, 0.038, 0.2, materialSkin, { x: 0.1, y: 0, z: 0 }, { x: 0, y: 0, z: -Math.PI / 2 })

    const rightHand = makeBone('RightHand', 0, 0.2, 0)
    rightForeArm.add(rightHand)
    attachCapsule(rightHand, 0.035, 0.08, materialJoint, { x: 0.04, y: 0, z: 0 }, { x: 0, y: 0, z: -Math.PI / 2 })

    // 左腿 (沿 -Y 方向自然向下)
    const leftUpLeg = makeBone('LeftUpLeg', -0.06, -0.09, 0)
    hips.add(leftUpLeg)
    attachJoint(leftUpLeg, 0.05)
    attachCapsule(leftUpLeg, 0.055, 0.36, materialSkin, { x: 0, y: -0.18, z: 0 })

    const leftLeg = makeBone('LeftLeg', -0.36, 0, 0)
    leftUpLeg.add(leftLeg)
    attachJoint(leftLeg, 0.045)
    attachCapsule(leftLeg, 0.048, 0.36, materialSkin, { x: 0, y: -0.18, z: 0 })

    const leftFoot = makeBone('LeftFoot', -0.36, 0, 0.04)
    leftLeg.add(leftFoot)
    attachCapsule(leftFoot, 0.04, 0.12, materialJoint, { x: 0, y: -0.02, z: 0 }, { x: Math.PI / 2, y: 0, z: 0 })

    // 右腿 (沿 -Y 方向自然向下)
    const rightUpLeg = makeBone('RightUpLeg', -0.06, 0.09, 0)
    hips.add(rightUpLeg)
    attachJoint(rightUpLeg, 0.05)
    attachCapsule(rightUpLeg, 0.055, 0.36, materialSkin, { x: 0, y: -0.18, z: 0 })

    const rightLeg = makeBone('RightLeg', -0.36, 0, 0)
    rightUpLeg.add(rightLeg)
    attachJoint(rightLeg, 0.045)
    attachCapsule(rightLeg, 0.048, 0.36, materialSkin, { x: 0, y: -0.18, z: 0 })

    const rightFoot = makeBone('RightFoot', -0.36, 0, 0.04)
    rightLeg.add(rightFoot)
    attachCapsule(rightFoot, 0.04, 0.12, materialJoint, { x: 0, y: -0.02, z: 0 }, { x: Math.PI / 2, y: 0, z: 0 })
  } else if (rigType === 'quadruped') {
    // ── 四足动物骨骼 ──
    const hips = makeBone('Hips', 0.6, 0, -0.2)
    group.add(hips)
    attachCapsule(hips, 0.12, 0.22, materialSkin)

    const spine = makeBone('Spine', 0, 0, 0.2)
    hips.add(spine)
    attachCapsule(spine, 0.13, 0.22, materialSkin, 0, Math.PI / 2)

    const spine1 = makeBone('Spine1', 0, 0, 0.2)
    spine.add(spine1)
    attachCapsule(spine1, 0.14, 0.24, materialSkin, 0, Math.PI / 2)

    const neck = makeBone('Neck', 0.15, 0, 0.15)
    spine1.add(neck)
    attachCapsule(neck, 0.07, 0.16, materialSkin, 0.08)

    const head = makeBone('Head', 0.12, 0, 0.1)
    neck.add(head)
    attachCapsule(head, 0.1, 0.18, materialSkin, 0.05)

    const jaw = makeBone('Jaw', -0.04, 0, 0.06)
    head.add(jaw)
    attachCapsule(jaw, 0.04, 0.08, materialJoint, 0)

    // 前腿 (左/右)
    const leftFrontLeg = makeBone('LeftFrontLeg', -0.05, -0.14, 0)
    spine1.add(leftFrontLeg)
    attachCapsule(leftFrontLeg, 0.04, 0.24, materialSkin, -0.12)

    const leftFrontKnee = makeBone('LeftFrontKnee', -0.24, 0, 0)
    leftFrontLeg.add(leftFrontKnee)
    attachCapsule(leftFrontKnee, 0.035, 0.24, materialSkin, -0.12)

    const leftFrontFoot = makeBone('LeftFrontFoot', -0.24, 0, 0.02)
    leftFrontKnee.add(leftFrontFoot)
    attachJoint(leftFrontFoot, 0.04)

    const rightFrontLeg = makeBone('RightFrontLeg', -0.05, 0.14, 0)
    spine1.add(rightFrontLeg)
    attachCapsule(rightFrontLeg, 0.04, 0.24, materialSkin, -0.12)

    const rightFrontKnee = makeBone('RightFrontKnee', -0.24, 0, 0)
    rightFrontLeg.add(rightFrontKnee)
    attachCapsule(rightFrontKnee, 0.035, 0.24, materialSkin, -0.12)

    const rightFrontFoot = makeBone('RightFrontFoot', -0.24, 0, 0.02)
    rightFrontKnee.add(rightFrontFoot)
    attachJoint(rightFrontFoot, 0.04)

    // 后腿 (左/右)
    const leftHindLeg = makeBone('LeftHindLeg', -0.05, -0.12, 0)
    hips.add(leftHindLeg)
    attachCapsule(leftHindLeg, 0.05, 0.24, materialSkin, -0.12)

    const leftHindKnee = makeBone('LeftHindKnee', -0.24, 0, 0)
    leftHindLeg.add(leftHindKnee)
    attachCapsule(leftHindKnee, 0.04, 0.24, materialSkin, -0.12)

    const leftHindFoot = makeBone('LeftHindFoot', -0.24, 0, 0.02)
    leftHindKnee.add(leftHindFoot)
    attachJoint(leftHindFoot, 0.04)

    const rightHindLeg = makeBone('RightHindLeg', -0.05, 0.12, 0)
    hips.add(rightHindLeg)
    attachCapsule(rightHindLeg, 0.05, 0.24, materialSkin, -0.12)

    const rightHindKnee = makeBone('RightHindKnee', -0.24, 0, 0)
    rightHindLeg.add(rightHindKnee)
    attachCapsule(rightHindKnee, 0.04, 0.24, materialSkin, -0.12)

    const rightHindFoot = makeBone('RightHindFoot', -0.24, 0, 0.02)
    rightHindKnee.add(rightHindFoot)
    attachJoint(rightHindFoot, 0.04)

    // 尾巴
    const tail = makeBone('Tail', 0.05, 0, -0.12)
    hips.add(tail)
    attachCapsule(tail, 0.03, 0.16, materialJoint, 0.08)

    const tail1 = makeBone('Tail1', 0.14, 0, -0.04)
    tail.add(tail1)
    attachCapsule(tail1, 0.025, 0.14, materialJoint, 0.07)

    const tail2 = makeBone('Tail2', 0.12, 0, -0.02)
    tail1.add(tail2)
    attachCapsule(tail2, 0.02, 0.12, materialJoint, 0.06)
  } else {
    // ── 通用多节脊椎骨骼 (Avian / Serpentine / Aquatic / Hexapod / Octopod) ──
    const hips = makeBone('Hips', 0.7, 0, 0)
    group.add(hips)
    attachCapsule(hips, 0.14, 0.2, materialSkin)

    let prevBone = hips

    for (let i = 1; i <= 4; i++) {
      const seg = makeBone(`Spine${i > 1 ? i : ''}`, 0.14, 0, 0)
      prevBone.add(seg)
      attachCapsule(seg, 0.12 - i * 0.015, 0.15, materialSkin, 0.07)
      attachJoint(seg, 0.05)
      prevBone = seg
    }

    const neck = makeBone('Neck', 0.14)
    prevBone.add(neck)
    attachCapsule(neck, 0.06, 0.1, materialSkin, 0.05)

    const head = makeBone('Head', 0.1)
    neck.add(head)
    attachCapsule(head, 0.1, 0.16, materialSkin, 0.08)

    // 翅膀或触手等附件
    const wingL = makeBone('LeftWing', 0, -0.15, 0)
    prevBone.add(wingL)
    attachCapsule(wingL, 0.03, 0.35, materialJoint, 0, Math.PI / 3)

    const wingR = makeBone('RightWing', 0, 0.15, 0)
    prevBone.add(wingR)
    attachCapsule(wingR, 0.03, 0.35, materialJoint, 0, -Math.PI / 3)
  }

  const { boneRestQuats, embeddedClips, morphTargets, stats } = inspectScene(
    group,
    'mannequin',
    `Mannequin (${rigType})`
  )

  return { root: group, bones, boneRestQuats, embeddedClips, morphTargets, stats }
}
