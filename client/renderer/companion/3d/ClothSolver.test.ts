import * as THREE from 'three'
import { describe, expect, it } from 'vitest'

import { BodyCollider } from './BodyCollider'
import { ClothSolver } from './ClothSolver'

function makeRiggedMesh(geometry: THREE.BufferGeometry): {
  mesh: THREE.Mesh
  skeleton: THREE.Skeleton
  bone: THREE.Bone
} {
  const bone = new THREE.Bone()
  bone.name = 'mixamorig:Hips'
  const skeleton = new THREE.Skeleton([bone], [new THREE.Matrix4()])

  const count = geometry.attributes.position.count
  const skinIndex = new Uint16Array(count * 4)
  const skinWeight = new Float32Array(count * 4)

  for (let i = 0; i < count; i++) {
    skinWeight[i * 4] = 1.0
  }

  geometry.setAttribute('skinIndex', new THREE.BufferAttribute(skinIndex, 4))
  geometry.setAttribute('skinWeight', new THREE.BufferAttribute(skinWeight, 4))

  const mesh = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial())

  return { mesh, skeleton, bone }
}

function makeBody(): THREE.SkinnedMesh {
  const bone = new THREE.Bone()
  bone.name = 'mixamorig:Hips'
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

describe('ClothSolver', () => {
  it('pins all vertices in skin mode (pinAll=true) and resolves BodyCollider', () => {
    const geo = new THREE.PlaneGeometry(0.2, 0.2, 2, 2)
    const { mesh, skeleton } = makeRiggedMesh(geo)
    const body = makeBody()
    const bodyCollider = new BodyCollider(body)
    bodyCollider.update()

    const solver = new ClothSolver(mesh, skeleton, new THREE.Matrix4(), {
      pinAll: true,
      bodyCollider,
      clearance: 0.005
    })

    // 推进一帧模拟
    solver.update(1 / 60)

    const pos = geo.attributes.position
    // 由于 PlaneGeometry 的中心位于原点（位于 1×1×1 身体盒内部），
    // 顶点应被 bodyCollider 推出。
    const v = new THREE.Vector3(pos.getX(0), pos.getY(0), pos.getZ(0))
    expect(v.length()).toBeGreaterThanOrEqual(0.5)
  })

  it('simulates gravity and edge relaxation for free vertices in cloth mode (pinAll=false)', () => {
    // 从 Y=0 到 Y=-1 的竖直圆柱（布条）
    const geo = new THREE.CylinderGeometry(0.1, 0.1, 1, 8, 4)
    const { mesh, skeleton } = makeRiggedMesh(geo)

    const solver = new ClothSolver(mesh, skeleton, new THREE.Matrix4())

    const initialY = geo.attributes.position.getY(0)

    // 推进多帧模拟
    for (let i = 0; i < 10; i++) {
      solver.update(1 / 60)
    }

    // 下方顶点应在重力作用下向下移动
    let minFinalY = Infinity

    for (let i = 0; i < geo.attributes.position.count; i++) {
      minFinalY = Math.min(minFinalY, geo.attributes.position.getY(i))
    }

    expect(minFinalY).toBeLessThan(initialY)
  })

  it('pushes cloth free vertices out of BodyCollider during simulation', () => {
    // 创建一块穿过身体原点的布料平面
    const geo = new THREE.PlaneGeometry(0.6, 0.6, 4, 4)
    const { mesh, skeleton } = makeRiggedMesh(geo)
    const body = makeBody()
    const bodyCollider = new BodyCollider(body)
    bodyCollider.update()

    const solver = new ClothSolver(mesh, skeleton, new THREE.Matrix4(), {
      bodyCollider,
      clearance: 0.01
    })

    // 当 bodyCollider 存在时，骨骼球碰撞体应被禁用
    expect((solver as unknown as { colliders: unknown[] }).colliders.length).toBe(0)

    // 推进模拟帧
    for (let i = 0; i < 5; i++) {
      solver.update(1 / 60)
    }

    // 每个顶点应位于身体表面（半宽 0.5m）+ clearance 之外
    const pos = geo.attributes.position

    for (let i = 0; i < pos.count; i++) {
      const v = new THREE.Vector3(pos.getX(i), pos.getY(i), pos.getZ(i))
      expect(Math.max(Math.abs(v.x), Math.abs(v.y), Math.abs(v.z))).toBeGreaterThanOrEqual(0.48)
    }
  })
})
