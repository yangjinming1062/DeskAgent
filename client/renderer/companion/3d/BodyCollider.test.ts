import * as THREE from 'three'
import { describe, expect, it } from 'vitest'

import { BodyCollider } from './BodyCollider'

function makeBody(): THREE.SkinnedMesh {
  const bone = new THREE.Bone()
  const skeleton = new THREE.Skeleton([bone], [new THREE.Matrix4()])
  const geo = new THREE.BoxGeometry(1, 1, 1)
  const count = geo.attributes.position.count
  const skinIndex = new Uint16Array(count * 4)
  const skinWeight = new Float32Array(count * 4)

  for (let i = 0; i < count; i++) {
    skinWeight[i * 4] = 1
  }

  geo.setAttribute('skinIndex', new THREE.BufferAttribute(skinIndex, 4))
  geo.setAttribute('skinWeight', new THREE.BufferAttribute(skinWeight, 4))

  const mesh = new THREE.SkinnedMesh(geo, new THREE.MeshStandardMaterial())
  mesh.bind(skeleton, new THREE.Matrix4())

  return mesh
}

describe('BodyCollider', () => {
  it('pushes an interior point out to the clearance surface', () => {
    const collider = new BodyCollider(makeBody())
    collider.update()

    const p = new THREE.Vector3(0, 0, 0)
    collider.resolve(p, 0.002)

    expect(Math.abs(p.length() - 0.502)).toBeLessThan(0.02)
  })

  it('leaves an exterior point untouched', () => {
    const collider = new BodyCollider(makeBody())
    collider.update()

    const p = new THREE.Vector3(2, 0, 0)
    collider.resolve(p, 0.002)

    expect(p.x).toBeCloseTo(2)
    expect(p.y).toBeCloseTo(0)
    expect(p.z).toBeCloseTo(0)
  })
})
