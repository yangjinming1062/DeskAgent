import type * as THREE from 'three'
import { type ComputeNode, StorageBufferAttribute } from 'three/webgpu'

import type { ClothUnitSpec, PhysicsBackend, PhysicsUnit } from './PhysicsBackend'
import { makeMat4Storage, type SharedBones, TslClothUnit } from './TslClothUnit'

// WebGPU 物理后端 —— 持有每个骨骼共享的骨骼矩阵 storage
// （除了微小 uniform 之外唯一的逐帧 CPU→GPU 上传），
// 并把每个活单元的 compute pass 聚合成引擎逐帧调度所需的形式。

const _staleCheck = (bm: Float32Array): boolean => bm.length >= 11 && bm[0] === 0 && bm[5] === 0 && bm[10] === 0

export class TslComputeBackend implements PhysicsBackend {
  readonly kind = 'tsl' as const

  private readonly units: TslClothUnit[] = []
  private readonly bonesBySkeleton = new Map<THREE.Skeleton, SharedBones>()

  createUnit(spec: ClothUnitSpec): PhysicsUnit | null {
    const pos = spec.mesh.geometry.getAttribute('position')

    if (!pos || pos.count === 0) {
      return null
    }

    let bones = this.bonesBySkeleton.get(spec.skeleton)

    if (!bones) {
      const attr = new StorageBufferAttribute(new Float32Array(spec.skeleton.bones.length * 16), 16)
      bones = { attr, node: makeMat4Storage(attr, spec.skeleton.bones.length) }
      this.bonesBySkeleton.set(spec.skeleton, bones)
    }

    const unit = new TslClothUnit(spec.mesh, spec.skeleton, spec.bindMatrix, spec.mode, bones)

    this.units.push(unit)

    return unit
  }

  destroyUnit(unit: PhysicsUnit): void {
    const idx = this.units.indexOf(unit as TslClothUnit)

    if (idx >= 0) {
      this.units.splice(idx, 1)
    }

    unit.dispose()
  }

  beginFrame(): void {
    // 每个骨骼一份矩阵快照，与 CPU 求解器一致：
    // 矩阵来自上一帧渲染通道（延迟一帧），尚未渲染过的骨骼（加载后首帧）显式更新，
    // 避免 settle pass 把所有内容压到原点。
    for (const [skeleton, bones] of this.bonesBySkeleton) {
      const source = skeleton.boneMatrices as unknown as Float32Array | undefined

      if (!source || _staleCheck(source)) {
        skeleton.update()
      }

      bones.attr.array.set(skeleton.boneMatrices as unknown as Float32Array)
      bones.attr.needsUpdate = true
    }

    for (const unit of this.units) {
      unit.prepareFrame()
    }
  }

  collectCompute(): ComputeNode[] {
    const nodes: ComputeNode[] = []

    for (const unit of this.units) {
      nodes.push(...unit.collectCompute())
    }

    return nodes
  }

  dispose(): void {
    for (const unit of this.units) {
      unit.dispose()
    }

    this.units.length = 0
    this.bonesBySkeleton.clear()
  }
}
