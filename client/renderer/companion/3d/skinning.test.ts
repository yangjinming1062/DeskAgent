import * as THREE from 'three'
import { describe, expect, it } from 'vitest'

import { cpuSkinPoint } from './skinning'

function identityBoneMatrices(count: number): Float32Array {
  const bm = new Float32Array(16 * count)

  for (let b = 0; b < count; b++) {
    const o = b * 16
    bm[o] = 1
    bm[o + 5] = 1
    bm[o + 10] = 1
    bm[o + 15] = 1
  }

  return bm
}

describe('cpuSkinPoint', () => {
  it('applies bindMatrix before bone weights (the bindMatrix≈identity fix)', () => {
    const bind = new THREE.Matrix4().makeTranslation(10, 0, 0)
    const out = new THREE.Vector3()

    cpuSkinPoint(1, 0, 0, [0, 0, 0, 0], [1, 0, 0, 0], 0, identityBoneMatrices(1), bind, out)

    expect(out.x).toBeCloseTo(11)
    expect(out.y).toBeCloseTo(0)
    expect(out.z).toBeCloseTo(0)
  })

  it('treats a null bindMatrix as identity', () => {
    const out = new THREE.Vector3()

    cpuSkinPoint(1, 2, 3, [0, 0, 0, 0], [1, 0, 0, 0], 0, identityBoneMatrices(1), null, out)

    expect(out.x).toBeCloseTo(1)
    expect(out.y).toBeCloseTo(2)
    expect(out.z).toBeCloseTo(3)
  })
})
