import * as THREE from 'three'
import {
  assign,
  compute,
  cross,
  float,
  Fn,
  If,
  instanceIndex,
  Loop,
  max,
  select,
  sqrt,
  storage,
  uint,
  uniform,
  uniformArray,
  vec3,
  vertexIndex
} from 'three/tsl'
import { type ComputeNode, MeshStandardNodeMaterial, NodeMaterial, StorageBufferAttribute } from 'three/webgpu'

import { log } from '@/shared/lib/log'

import { boneSuffix } from '../types'

import {
  buildAnchors,
  buildConstraints,
  buildVertexTriAdjacency,
  COLLIDER_RADII,
  DAMPING,
  GRAVITY,
  ITERATIONS
} from './cloth-topology'
import type { PhysicsUnit } from './PhysicsBackend'

// GPU 布料 / 蒙皮单元 —— 用 TSL compute pass 镜像 ClothSolver 语义：
// skin + integrate → 距离约束 → 骨骼球碰撞 → 法线。
// 所有世界空间输入（骨骼矩阵、碰撞体位置）都在着色器里通过 uMeshInv
// 转到网格的局部空间，因此 `pos` storage 存的是局部坐标，
// 渲染通道恰好应用一次 modelWorldMatrix —— 与 geometry.attributes.position 的契约一致。

// @types/three 缺少这个静态工厂的类型声明；
// 但它在 three.webgpu.js 里存在，可把任意经典材质
// （MeshStandardMaterial → MeshStandardNodeMaterial）转成节点材质。
const fromMaterial = (
  NodeMaterial as unknown as {
    fromMaterial: (m: THREE.Material) => MeshStandardNodeMaterial
  }
).fromMaterial

export const makeMat4Storage = (attr: StorageBufferAttribute, count: number) => storage(attr, 'mat4', count)

export interface SharedBones {
  attr: StorageBufferAttribute
  node: ReturnType<typeof makeMat4Storage>
}

type Vec4ArrayNode = ReturnType<typeof uniformArray<'vec4'>>

const _boneWorld = new THREE.Vector3()

export class TslClothUnit implements PhysicsUnit {
  private readonly mesh: THREE.Mesh
  private readonly colliders: { bone: THREE.Bone; radius: number; value: THREE.Vector4 }[] = []

  private readonly uDt = uniform(1 / 60)
  private readonly uSettle = uniform(1)
  private readonly uDamping = uniform(DAMPING)
  private readonly uGravity = uniform(GRAVITY)
  private readonly uMeshInv = uniform(new THREE.Matrix4())
  private readonly uBindMatrix = uniform(new THREE.Matrix4())
  private readonly uColliderCount = uniform(0, 'uint')
  private readonly uColliders: Vec4ArrayNode

  private readonly attrs: StorageBufferAttribute[] = []
  private readonly passes: ComputeNode[] = []
  private readonly normalsPass: ComputeNode | null
  private readonly skinPass: ComputeNode | null

  private pendingSettle = true
  private frameTick = 0

  constructor(
    mesh: THREE.Mesh,
    skeleton: THREE.Skeleton,
    bindMatrix: THREE.Matrix4 | null,
    mode: 'cloth' | 'skin',
    bones: SharedBones
  ) {
    this.mesh = mesh
    this.uBindMatrix.value.copy(bindMatrix ?? new THREE.Matrix4())

    const geo = mesh.geometry
    const posAttr = geo.getAttribute('position')
    const si = geo.getAttribute('skinIndex')
    const sw = geo.getAttribute('skinWeight')
    const count = posAttr?.count ?? 0

    if (!posAttr || count === 0 || !si || !sw) {
      // 对 GPU 模拟来说已退化（通常是无蒙皮的静态片段）—— 调用方继续原样渲染网格。
      this.uColliders = makeVec4Array([])
      this.normalsPass = null
      this.skinPass = null

      return
    }

    const base = new Float32Array(posAttr.array as ArrayLike<number>)
    const anchors = buildAnchors(base, count, mode === 'skin')

    const mkAttr = (array: Float32Array | Uint32Array, itemSize: number): StorageBufferAttribute => {
      const attr = new StorageBufferAttribute(array, itemSize)

      this.attrs.push(attr)

      return attr
    }

    const baseStore = storage(mkAttr(base, 3), 'vec3', count)
    const posStore = storage(mkAttr(new Float32Array(base), 3), 'vec3', count)
    const prevStore = storage(mkAttr(new Float32Array(base), 3), 'vec3', count)

    const normalSource = geo.getAttribute('normal')

    const normalStore = storage(
      mkAttr(new Float32Array((normalSource?.array as Float32Array) ?? new Float32Array(count * 3)), 3),
      'vec3',
      count
    )

    const anchorStore = storage(mkAttr(new Uint32Array(anchors), 1), 'uint', count)
    const skinIndexStore = storage(mkAttr(new Uint32Array(si.array as ArrayLike<number>), 4), 'uvec4', count)
    const skinWeightStore = storage(mkAttr(new Float32Array(sw.array as ArrayLike<number>), 4), 'vec4', count)
    const boneStore = bones.node

    // ── Pass 1：蒙皮目标 + Verlet 积分 ────────────────────────
    // skinned = meshInv · Σ wᵢ · boneMatricesᵢ · (bindMatrix · base) ——
    // 与 skinning.ts::cpuSkinPoint 的公式相同。
    // 首次步进（Settle）把每个顶点都钉住，避免布料从绑定姿态突然弹开。
    this.skinPass = computeNamed(
      'clothSkin',
      Fn(() => {
        const i = instanceIndex
        const bind = this.uBindMatrix.mul(baseStore.element(i))
        const si4 = skinIndexStore.element(i)
        const sw4 = skinWeightStore.element(i)

        const skinned = vec3(0.0).toVar()
        assign(skinned, skinned.add(sw4.x.mul(boneStore.element(uint(si4.x)).mul(bind))))
        assign(skinned, skinned.add(sw4.y.mul(boneStore.element(uint(si4.y)).mul(bind))))
        assign(skinned, skinned.add(sw4.z.mul(boneStore.element(uint(si4.z)).mul(bind))))
        assign(skinned, skinned.add(sw4.w.mul(boneStore.element(uint(si4.w)).mul(bind))))
        assign(skinned, this.uMeshInv.mul(skinned))

        const anchored = anchorStore
          .element(i)
          .greaterThanEqual(uint(1))
          .or(this.uSettle.greaterThan(float(0.5)))

        If(anchored, () => {
          assign(posStore.element(i), skinned)
          assign(prevStore.element(i), skinned)
        }).Else(() => {
          const cur = posStore.element(i)
          const vel = cur.sub(prevStore.element(i)).mul(this.uDamping)
          const g = this.uGravity.mul(this.uDt.mul(this.uDt))

          assign(prevStore.element(i), cur)
          assign(posStore.element(i), cur.add(vel).add(vec3(0.0, g, 0.0)))
        })
      })(),
      count
    )

    this.passes.push(this.skinPass)

    if (mode === 'cloth') {
      const idx = geo.index?.array

      if (idx) {
        const constraints = buildConstraints(idx, count, base)

        if (constraints) {
          const edgeCount = constraints.edges.length / 2
          const edgesStore = storage(mkAttr(new Uint32Array(constraints.edges), 2), 'uvec2', edgeCount)
          const restStore = storage(mkAttr(new Float32Array(constraints.rest), 1), 'float', edgeCount)

          // ── Pass 2：距离约束松弛 ──────────────────────
          // 一次 dispatch 内的并行调用共享端点，存在竞争；dispatch 之间写入收敛，
          // 因此三次 pass 类似 Jacobi / Gauss-Seidel 混合迭代收敛。
          // 固定端吸收全部修正量，与 CPU 端一致。
          const constraintPass = computeNamed(
            'clothConstraint',
            Fn(() => {
              const e = instanceIndex
              const a = edgesStore.element(e).x
              const b = edgesStore.element(e).y
              const pa = posStore.element(a)
              const pb = posStore.element(b)
              const d = pb.sub(pa)
              const dist = d.length()
              const rest = restStore.element(e)

              If(dist.greaterThan(float(1e-6)).and(dist.sub(rest).abs().greaterThan(float(1e-9))), () => {
                const diff = dist.sub(rest).div(dist)
                const aFixed = anchorStore.element(a).greaterThanEqual(uint(1))
                const bFixed = anchorStore.element(b).greaterThanEqual(uint(1))
                const wa = select(aFixed, float(0.0), select(bFixed, float(1.0), float(0.5)))
                const wb = select(bFixed, float(0.0), select(aFixed, float(1.0), float(0.5)))

                assign(posStore.element(a), pa.add(d.mul(diff.mul(wa))))
                assign(posStore.element(b), pb.sub(d.mul(diff.mul(wb))))
              })
            })(),
            edgeCount
          )

          for (let it = 0; it < ITERATIONS; it++) {
            this.passes.push(constraintPass)
          }
        } else {
          log.warn('cloth', 'GPU cloth mesh exceeds vertex budget — constraints disabled')
        }
      } else {
        log.warn('cloth', 'GPU cloth mesh has no index buffer — constraints disabled')
      }

      // ── Pass 3：骨骼球碰撞（仅作用于自由顶点）───────────────
      // 当没有任何骨骼命中半径表（非人形骨骼）时整段跳过：
      // 长度为 0 的 uniform 数组不是合法的 WGSL。
      for (const bone of skeleton.bones) {
        const radius = COLLIDER_RADII[boneSuffix(bone.name)]

        if (radius !== undefined) {
          this.colliders.push({ bone, radius, value: new THREE.Vector4(0, 0, 0, radius) })
        }
      }

      this.uColliders = makeVec4Array(this.colliders.map(c => c.value))

      if (this.colliders.length > 0) {
        this.uColliderCount.value = this.colliders.length

        this.passes.push(
          computeNamed(
            'clothCollide',
            Fn(() => {
              const i = instanceIndex

              If(anchorStore.element(i).equal(uint(0)), () => {
                const p = posStore.element(i).toVar()

                Loop({ start: uint(0), end: this.uColliderCount }, ({ i: ci }) => {
                  const c = this.uColliders.element(ci)
                  const dvec = p.sub(c.xyz)
                  const d2 = dvec.dot(dvec)
                  const r2 = c.w.mul(c.w)

                  If(d2.lessThan(r2).and(d2.greaterThan(float(1e-9))), () => {
                    const dist = sqrt(d2)
                    const push = c.w.sub(dist).div(dist)

                    assign(p, p.add(dvec.mul(push)))
                  })
                })

                assign(posStore.element(i), p)
              })
            })(),
            count
          )
        )
      }
    } else {
      this.uColliders = makeVec4Array([])
    }

    // ── Pass 4：每两帧由累积面法线重算法线────────
    // 按顶点的三角邻接关系保证写入互不重叠（无需原子操作）；
    // 除法做夹紧，避免零面积三角形把零向量归一化成 NaN 黑色斑点。
    const triIndex = geo.index?.array

    if (triIndex) {
      const adjacency = buildVertexTriAdjacency(triIndex, count)
      const triCount = Math.floor(triIndex.length / 3)
      const triIndexStore = storage(mkAttr(new Uint32Array(triIndex as ArrayLike<number>), 3), 'uvec3', triCount)
      const offsetsStore = storage(mkAttr(new Uint32Array(adjacency.offsets), 1), 'uint', count + 1)
      const listStore = storage(mkAttr(new Uint32Array(adjacency.list), 1), 'uint', adjacency.list.length)

      this.normalsPass = computeNamed(
        'clothNormals',
        Fn(() => {
          const i = instanceIndex
          const acc = vec3(0.0).toVar()

          Loop({ start: offsetsStore.element(i), end: offsetsStore.element(uint(i).add(uint(1))) }, ({ i: k }) => {
            const tri = triIndexStore.element(listStore.element(k))
            const va = posStore.element(tri.x)
            const vb = posStore.element(tri.y)
            const vc = posStore.element(tri.z)

            assign(acc, acc.add(cross(vb.sub(va), vc.sub(va))))
          })

          assign(normalStore.element(i), acc.div(max(acc.length(), float(1e-6))))
        })(),
        count
      )
    } else {
      this.normalsPass = null
    }

    // 渲染通道直接读取 storage —— positionNode / normalNode 读局部空间值，
    // 所以 modelWorldMatrix 恰好应用一次。
    const readPos = posStore.toReadOnly().element(vertexIndex)
    const readNormal = normalStore.toReadOnly().element(vertexIndex)
    const originals = Array.isArray(mesh.material) ? mesh.material : [mesh.material]

    const converted = originals.map(m => {
      const nodeMat = fromMaterial(m)

      nodeMat.positionNode = readPos
      nodeMat.normalNode = readNormal

      return nodeMat
    })

    mesh.material = converted.length === 1 ? converted[0] : converted
  }

  /** Refresh world→local uniforms + local-space collider spheres. */
  prepareFrame(): void {
    if (!this.skinPass) {
      return
    }

    this.mesh.updateWorldMatrix(true, false)
    this.uMeshInv.value.copy(this.mesh.matrixWorld).invert()

    for (const c of this.colliders) {
      _boneWorld.setFromMatrixPosition(c.bone.matrixWorld).applyMatrix4(this.uMeshInv.value)
      c.value.set(_boneWorld.x, _boneWorld.y, _boneWorld.z, c.radius)
    }
  }

  step(dt: number): void {
    if (!this.skinPass) {
      return
    }

    this.uDt.value = Math.min(Math.max(dt, 1 / 120), 1 / 30)
    this.uSettle.value = this.pendingSettle ? 1 : 0
    this.pendingSettle = false
    this.frameTick++
  }

  collectCompute(): ComputeNode[] {
    if (!this.skinPass) {
      return []
    }

    // 把法线重算减半 —— 60fps 的衣摆看起来与 30fps 一模一样
    // （与 CPU 求解器同样的权衡，并且这是开销最大的 pass）。
    if (this.frameTick % 2 === 0 && this.normalsPass) {
      return [...this.passes, this.normalsPass]
    }

    return this.passes
  }

  dispose(): void {
    for (const attr of this.attrs) {
      attr.dispose()
    }

    const mats = Array.isArray(this.mesh.material) ? this.mesh.material : [this.mesh.material]

    for (const m of mats) {
      if (m instanceof MeshStandardNodeMaterial) {
        m.dispose()
      }
    }
  }
}

function makeVec4Array(values: THREE.Vector4[]): Vec4ArrayNode {
  return uniformArray(values, 'vec4')
}

function computeNamed(name: string, node: Parameters<typeof compute>[0], count: number): ComputeNode {
  return compute(node, count).setName(name)
}
