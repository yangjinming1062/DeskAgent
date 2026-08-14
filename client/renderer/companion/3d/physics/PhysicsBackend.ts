import type * as THREE from 'three'
import type { ComputeNode } from 'three/webgpu'

import type { BodyCollider } from '../BodyCollider'
import type { EngineBackendKind } from '../types'

// PhysicsBackend decouples CharacterController from where cloth/skin solving
// happens: the WebGPU backend runs TSL compute passes, everything else keeps
// the main-thread CPU solver (behaviour identical to a WebGL-only build).

export type PhysicsMode = 'cloth' | 'skin'

export interface ClothUnitSpec {
  /** Render mesh; the TSL backend replaces its material with node materials
   * whose positionNode/normalNode read the compute-written storage. */
  mesh: THREE.Mesh
  skeleton: THREE.Skeleton
  bindMatrix: THREE.Matrix4 | null
  mode: PhysicsMode
  /** CPU backend only — body-surface collision proxy. The TSL backend uses
   * bone-sphere colliders instead (BVH mesh collision stays CPU-only). */
  bodyCollider?: BodyCollider | null
}

export interface PhysicsUnit {
  step(dt: number): void
  dispose(): void
}

export type PhysicsBackendKind = 'tsl' | 'cpu'

export interface PhysicsBackend {
  readonly kind: PhysicsBackendKind
  /** null when the unit can't be simulated (no positions / over budget / no
   * skin weights on the GPU path) — the caller keeps the mesh static. */
  createUnit(spec: ClothUnitSpec): PhysicsUnit | null
  /** Tear down one unit and drop it from the per-frame dispatch — called
   * from CharacterController.disposeUnit (outfit swap / model reload). */
  destroyUnit(unit: PhysicsUnit): void
  /** Per-frame CPU→GPU uploads (bone-matrix snapshot, per-unit transforms). */
  beginFrame(): void
  collectCompute(): ComputeNode[]
  dispose(): void
}

export function pickBackendFor(kind: EngineBackendKind): PhysicsBackendKind {
  // The WebGL2 fallback backend technically implements compute via transform
  // feedback, but iGPU throughput there is not dependable — keep those paths
  // on the CPU solver instead of a half-fast GPU path.
  return kind === 'webgpu' ? 'tsl' : 'cpu'
}
