import type * as THREE from 'three'
import { type ComputeNode, StorageBufferAttribute } from 'three/webgpu'

import type { ClothUnitSpec, PhysicsBackend, PhysicsUnit } from './PhysicsBackend'
import { makeMat4Storage, type SharedBones, TslClothUnit } from './TslClothUnit'

// WebGPU physics backend — owns the shared per-skeleton bone-matrix storage
// (the only per-frame CPU→GPU upload beyond tiny uniforms) and aggregates the
// compute passes of every live unit for the engine's per-frame dispatch.

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
    // One bone-matrix snapshot per skeleton mirrors the CPU solver's contract:
    // matrices come from the previous render pass (one frame of lag), and a
    // never-rendered skeleton (first frame after load) is updated explicitly
    // so the settle pass doesn't collapse everything to the origin.
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
