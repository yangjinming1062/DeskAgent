import * as THREE from 'three'
import { describe, expect, it } from 'vitest'

import { CpuBackend } from './CpuBackend'
import { pickBackendFor } from './PhysicsBackend'

function riggedMesh(w = 0.2, h = 0.4): { mesh: THREE.Mesh; skeleton: THREE.Skeleton } {
  const geo = new THREE.PlaneGeometry(w, h, 2, 2)
  const bone = new THREE.Bone()
  bone.name = 'mixamorig:Hips'
  const skeleton = new THREE.Skeleton([bone], [new THREE.Matrix4()])
  const count = geo.attributes.position.count
  const skinIndex = new Uint16Array(count * 4)
  const skinWeight = new Float32Array(count * 4)

  for (let i = 0; i < count; i++) {
    skinWeight[i * 4] = 1.0
  }

  geo.setAttribute('skinIndex', new THREE.BufferAttribute(skinIndex, 4))
  geo.setAttribute('skinWeight', new THREE.BufferAttribute(skinWeight, 4))

  return { mesh: new THREE.Mesh(geo, new THREE.MeshStandardMaterial()), skeleton }
}

describe('pickBackendFor', () => {
  it('maps renderer kinds to physics backends deterministically', () => {
    expect(pickBackendFor('webgpu')).toBe('tsl')
    expect(pickBackendFor('webgl2')).toBe('cpu')
    expect(pickBackendFor('classic-webgl')).toBe('cpu')
  })
})

describe('CpuBackend', () => {
  it('creates a stepping unit that delegates to the CPU solver', () => {
    const { mesh, skeleton } = riggedMesh()
    const backend = new CpuBackend()
    const unit = backend.createUnit({ mesh, skeleton, bindMatrix: null, mode: 'cloth' })

    expect(unit).not.toBeNull()

    // Stepping must not throw and must leave the geometry positions finite.
    unit!.step(1 / 60)

    const pos = mesh.geometry.attributes.position.array as Float32Array

    expect(pos.every(Number.isFinite)).toBe(true)

    unit!.dispose()
  })

  it('keeps compute dispatch empty', () => {
    expect(new CpuBackend().collectCompute()).toEqual([])
  })
})
