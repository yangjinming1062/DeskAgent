import type { ComputeNode } from 'three/webgpu'

import { ClothSolver } from '../ClothSolver'

import type { ClothUnitSpec, PhysicsBackend, PhysicsUnit } from './PhysicsBackend'

/** Main-thread solver — the exact behaviour of a WebGL-only build. */
export class CpuBackend implements PhysicsBackend {
  readonly kind = 'cpu' as const

  createUnit(spec: ClothUnitSpec): PhysicsUnit | null {
    const solver = new ClothSolver(spec.mesh, spec.skeleton, spec.bindMatrix, {
      pinAll: spec.mode === 'skin',
      bodyCollider: spec.bodyCollider ?? null
    })

    return {
      step: dt => solver.update(dt),
      dispose: () => {}
    }
  }

  destroyUnit(_unit: PhysicsUnit): void {}

  beginFrame(): void {}

  collectCompute(): ComputeNode[] {
    return []
  }

  dispose(): void {}
}
