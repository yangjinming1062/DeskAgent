import * as THREE from 'three'

/**
 * 骨骼与关节可视化。
 *
 * 与 THREE.SkeletonHelper 的细线不同，这里绘制的是实体关节球 + 锥形骨段，
 * 并且强制关闭深度测试叠加在模型之上，配合全息材质用于肉眼校验绑骨是否正确：
 * - 关节球位置是否落在解剖学正确的位置（肩/肘/腕/膝…）
 * - 骨段朝向是否指向子关节
 * - 左右侧骨骼按颜色区分，便于发现镜像错误
 */

/** 骨段朝向 +Y、底部位于原点、单位长度的锥形骨头几何体 */
function createBoneGeometry(): THREE.BufferGeometry {
  const ringY = 0.18

  const ring: [number, number, number][] = [
    [1, ringY, 0],
    [0, ringY, 1],
    [-1, ringY, 0],
    [0, ringY, -1]
  ]

  const base: [number, number, number] = [0, 0, 0]
  const tip: [number, number, number] = [0, 1, 0]

  const positions: number[] = []

  const push = (v: [number, number, number]) => positions.push(v[0], v[1], v[2])

  for (let i = 0; i < 4; i += 1) {
    const a = ring[i]
    const b = ring[(i + 1) % 4]

    // 底部四棱锥
    push(base)
    push(b)
    push(a)

    // 顶部长锥
    push(a)
    push(b)
    push(tip)
  }

  const geo = new THREE.BufferGeometry()
  geo.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3))
  geo.computeVertexNormals()

  return geo
}

/** 依据骨骼命名推断左右侧，用于着色区分（发现镜像/串权错误） */
function classifyBone(name: string): THREE.Color {
  const n = name.toLowerCase()

  if (/(^|[._-])(l|left)([._-]|\d|$)/.test(n)) {
    return new THREE.Color(0x38bdf8) // 左：天蓝
  }

  if (/(^|[._-])(r|right)([._-]|\d|$)/.test(n)) {
    return new THREE.Color(0xfb923c) // 右：橙
  }

  return new THREE.Color(0x4ade80) // 中轴：绿
}

interface BoneLink {
  parent: THREE.Bone
  child: THREE.Bone
  color: THREE.Color
}

export interface SkeletonViz {
  group: THREE.Group
  boneCount: number
  /** 每帧调用，根据骨骼世界矩阵刷新关节球与骨段实例 */
  update: () => void
  setVisible: (showBones: boolean, showJoints: boolean) => void
  dispose: () => void
}

/**
 * 为角色根节点构建骨骼可视化对象。
 * 返回的 group 需由调用方加入场景（世界空间，不作为角色子节点，避免受角色缩放二次影响）。
 */
export function createSkeletonViz(root: THREE.Object3D): SkeletonViz | null {
  const bones: THREE.Bone[] = []

  root.traverse(obj => {
    if ((obj as THREE.Bone).isBone) {
      bones.push(obj as THREE.Bone)
    }
  })

  if (bones.length === 0) {
    return null
  }

  const boneSet = new Set(bones)
  const links: BoneLink[] = []

  for (const bone of bones) {
    const parent = bone.parent

    if (parent && boneSet.has(parent as THREE.Bone)) {
      links.push({ parent: parent as THREE.Bone, child: bone, color: classifyBone(bone.name) })
    }
  }

  // 基准尺寸：以模型包围盒高度推算关节球半径，保证不同体型下视觉比例一致
  const box = new THREE.Box3().setFromObject(root)
  const height = box.isEmpty() ? 1.7 : Math.max(0.2, box.max.y - box.min.y)
  const jointRadius = height * 0.012
  const boneRadius = height * 0.008

  const group = new THREE.Group()
  group.name = 'SkeletonViz'
  group.renderOrder = 999

  const overlayMat = (): THREE.MeshBasicMaterialParameters => ({
    depthTest: false,
    depthWrite: false,
    transparent: true,
    toneMapped: false
  })

  const jointGeo = new THREE.SphereGeometry(1, 12, 10)
  const jointMat = new THREE.MeshBasicMaterial({ ...overlayMat(), opacity: 0.95 })
  const joints = new THREE.InstancedMesh(jointGeo, jointMat, bones.length)
  joints.frustumCulled = false
  joints.renderOrder = 1001
  group.add(joints)

  const boneGeo = createBoneGeometry()
  const boneMat = new THREE.MeshBasicMaterial({ ...overlayMat(), opacity: 0.55, side: THREE.DoubleSide })
  const boneMesh = new THREE.InstancedMesh(boneGeo, boneMat, Math.max(1, links.length))
  boneMesh.frustumCulled = false
  boneMesh.renderOrder = 1000
  boneMesh.count = links.length
  group.add(boneMesh)

  // 静态着色：关节球按左右侧上色，根骨骼高亮为品红
  bones.forEach((bone, i) => {
    const color =
      bone.parent && boneSet.has(bone.parent as THREE.Bone) ? classifyBone(bone.name) : new THREE.Color(0xf472b6)

    joints.setColorAt(i, color)
  })

  if (joints.instanceColor) {
    joints.instanceColor.needsUpdate = true
  }

  links.forEach((link, i) => boneMesh.setColorAt(i, link.color))

  if (boneMesh.instanceColor) {
    boneMesh.instanceColor.needsUpdate = true
  }

  const pA = new THREE.Vector3()
  const pB = new THREE.Vector3()
  const dir = new THREE.Vector3()
  const quat = new THREE.Quaternion()
  const scaleVec = new THREE.Vector3()
  const matrix = new THREE.Matrix4()
  const up = new THREE.Vector3(0, 1, 0)
  const identityQuat = new THREE.Quaternion()
  const rootScale = new THREE.Vector3()

  const update = () => {
    root.updateWorldMatrix(true, true)
    root.getWorldScale(rootScale)

    // 角色整体缩放后同步放大关节尺寸，避免缩小模型时骨骼球糊成一团
    const s = Math.max(1e-4, (Math.abs(rootScale.x) + Math.abs(rootScale.y) + Math.abs(rootScale.z)) / 3)

    for (let i = 0; i < bones.length; i += 1) {
      bones[i].getWorldPosition(pA)
      matrix.compose(pA, identityQuat, scaleVec.setScalar(jointRadius * s))
      joints.setMatrixAt(i, matrix)
    }

    joints.instanceMatrix.needsUpdate = true

    for (let i = 0; i < links.length; i += 1) {
      links[i].parent.getWorldPosition(pA)
      links[i].child.getWorldPosition(pB)
      dir.subVectors(pB, pA)

      const len = dir.length()

      if (len < 1e-6) {
        matrix.makeScale(0, 0, 0)
        matrix.setPosition(pA)
        boneMesh.setMatrixAt(i, matrix)

        continue
      }

      quat.setFromUnitVectors(up, dir.divideScalar(len))
      scaleVec.set(boneRadius * s, len, boneRadius * s)
      matrix.compose(pA, quat, scaleVec)
      boneMesh.setMatrixAt(i, matrix)
    }

    boneMesh.instanceMatrix.needsUpdate = true
  }

  update()

  return {
    group,
    boneCount: bones.length,
    update,
    setVisible: (showBones, showJoints) => {
      group.visible = showBones || showJoints
      boneMesh.visible = showBones
      joints.visible = showJoints
    },
    dispose: () => {
      jointGeo.dispose()
      boneGeo.dispose()
      jointMat.dispose()
      boneMat.dispose()
      group.removeFromParent()
    }
  }
}

/** 全息材质：半透明 + 边缘菲涅尔发光，便于透视观察内部骨骼 */
export function createHologramMaterial(): THREE.MeshStandardMaterial {
  const mat = new THREE.MeshStandardMaterial({
    color: 0x0e7490,
    emissive: 0x22d3ee,
    emissiveIntensity: 0.6,
    roughness: 0.35,
    metalness: 0.0,
    transparent: true,
    opacity: 0.22,
    depthWrite: false,
    side: THREE.FrontSide
  })

  // 视线掠射角处增强自发光，形成全息边缘辉光（skinning / morph 由标准材质自动处理）
  mat.onBeforeCompile = shader => {
    shader.fragmentShader = shader.fragmentShader.replace(
      '#include <emissivemap_fragment>',
      `#include <emissivemap_fragment>
      float _fres = 1.0 - abs(dot(normalize(vNormal), normalize(vViewPosition)));
      totalEmissiveRadiance *= 0.25 + pow(_fres, 2.5) * 4.0;`
    )
  }

  return mat
}
