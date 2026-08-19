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
  it('把渲染器类型确定性映射到物理后端', () => {
    expect(pickBackendFor('webgpu')).toBe('tsl')
    expect(pickBackendFor('webgl2')).toBe('cpu')
    expect(pickBackendFor('classic-webgl')).toBe('cpu')
  })
})

describe('CpuBackend', () => {
  it('创建一个把求解委托给 CPU 求解器的可步进单元', () => {
    const { mesh, skeleton } = riggedMesh()
    const backend = new CpuBackend()
    const unit = backend.createUnit({ mesh, skeleton, bindMatrix: null, mode: 'cloth' })

    expect(unit).not.toBeNull()

    // 步进必须不抛异常，并且让几何位置保持有限值。
    unit!.step(1 / 60)

    const pos = mesh.geometry.attributes.position.array as Float32Array

    expect(pos.every(Number.isFinite)).toBe(true)

    unit!.dispose()
  })

  it('保持 compute 派发表为空', () => {
    expect(new CpuBackend().collectCompute()).toEqual([])
  })
})
