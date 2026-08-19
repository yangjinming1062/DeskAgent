import type * as THREE from 'three'
import type { ComputeNode } from 'three/webgpu'

import type { BodyCollider } from '../BodyCollider'
import type { EngineBackendKind } from '../types'

// PhysicsBackend 把 CharacterController 与布料/蒙皮求解位置解耦：
// WebGPU 后端跑 TSL compute pass，其他情况一律保留主线程 CPU 求解器
// （行为与纯 WebGL 构建一致）。

export type PhysicsMode = 'cloth' | 'skin'

export interface ClothUnitSpec {
  /** 渲染网格；TSL 后端会把它的材质替换为节点材质，
   * 其 positionNode / normalNode 读取 compute 写入的 storage。 */
  mesh: THREE.Mesh
  skeleton: THREE.Skeleton
  bindMatrix: THREE.Matrix4 | null
  mode: PhysicsMode
  /** 仅 CPU 后端需要：身体表面碰撞代理。TSL 后端用骨骼球碰撞代替（BVH 网格碰撞仍只在 CPU）。 */
  bodyCollider?: BodyCollider | null
}

export interface PhysicsUnit {
  step(dt: number): void
  dispose(): void
}

export type PhysicsBackendKind = 'tsl' | 'cpu'

export interface PhysicsBackend {
  readonly kind: PhysicsBackendKind
  /** 当该单元无法模拟时（无位置 / 超出预算 / GPU 路径缺少蒙皮权重）返回 null，
   * 调用方保持网格静止。 */
  createUnit(spec: ClothUnitSpec): PhysicsUnit | null
  /** 销毁一个单元，并从逐帧调度里移除 —— 由 CharacterController.disposeUnit
   * 调用（换装 / 模型重载）。 */
  destroyUnit(unit: PhysicsUnit): void
  /** 逐帧 CPU → GPU 上传（骨骼矩阵快照、各单元的变换）。 */
  beginFrame(): void
  collectCompute(): ComputeNode[]
  dispose(): void
}

export function pickBackendFor(kind: EngineBackendKind): PhysicsBackendKind {
  // WebGL2 降级后端理论上可以用 transform feedback 实现 compute，但集显上吞吐不稳定
  // —— 干脆让那些路径继续走 CPU 求解器，而不是用半吊子的 GPU 路径。
  return kind === 'webgpu' ? 'tsl' : 'cpu'
}
