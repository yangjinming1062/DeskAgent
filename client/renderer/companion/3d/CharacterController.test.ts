import * as THREE from 'three'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { CharacterController } from './CharacterController'
import { clearAllGltf, gltfCacheStats, stashGltf } from './gltf-instance-cache'

function createFakeGlbModel(name: string): {
  animations: THREE.AnimationClip[]
  geometry: THREE.BufferGeometry
  material: THREE.MeshStandardMaterial
  root: THREE.Group
  texture: THREE.Texture
} {
  const root = new THREE.Group()
  const bone = new THREE.Bone()
  bone.name = 'Head'
  root.add(bone)

  const geometry = new THREE.BufferGeometry()
  const texture = new THREE.Texture()
  const material = new THREE.MeshStandardMaterial({ map: texture, name })
  const mesh = new THREE.SkinnedMesh(geometry, material)
  const skeleton = new THREE.Skeleton([bone])
  mesh.bind(skeleton)
  root.add(mesh)

  const clip = new THREE.AnimationClip('idle', 1, [
    new THREE.VectorKeyframeTrack('Head.position', [0, 1], [0, 0, 0, 0, 1, 0])
  ])

  return { animations: [clip], geometry, material, root, texture }
}

describe('CharacterController - Resource Ownership & Cache lifecycle', () => {
  let scene: THREE.Scene
  let controller: CharacterController

  beforeEach(() => {
    clearAllGltf(true)
    scene = new THREE.Scene()
    controller = new CharacterController()
  })

  it('连续切换模型与缓存命中时，不提前释放模板共享的 GPU 资源', async () => {
    const modelA = createFakeGlbModel('ModelA')
    const modelB = createFakeGlbModel('ModelB')

    const aGeoSpy = vi.spyOn(modelA.geometry, 'dispose')
    const aMatSpy = vi.spyOn(modelA.material, 'dispose')
    const aTexSpy = vi.spyOn(modelA.texture, 'dispose')

    const bGeoSpy = vi.spyOn(modelB.geometry, 'dispose')
    const bMatSpy = vi.spyOn(modelB.material, 'dispose')

    // 将 Model A 和 Model B 存入模板缓存
    stashGltf('hash-a', modelA.root, modelA.animations, 1024)
    stashGltf('hash-b', modelB.root, modelB.animations, 1024)

    // 1. 加载 Model A（从缓存命中深克隆）
    const infoA = await controller.load(null, scene, 'biped', 'hash-a')
    expect(infoA.procedural).toBe(false)
    expect(gltfCacheStats().activeRefs).toBe(1)
    expect(controller.root.parent).toBe(scene)

    // 2. 切换加载 Model B（从缓存命中深克隆，卸载 Model A）
    const infoB = await controller.load(null, scene, 'biped', 'hash-b')
    expect(infoB.procedural).toBe(false)
    expect(gltfCacheStats().activeRefs).toBe(1)

    // 关键断言：卸载 Model A 时，严禁释放 Model A 模板拥有的 geometry, material, texture
    expect(aGeoSpy).not.toHaveBeenCalled()
    expect(aMatSpy).not.toHaveBeenCalled()
    expect(aTexSpy).not.toHaveBeenCalled()

    // 3. 再次加载 Model A（再次从缓存命中深克隆）
    const infoA2 = await controller.load(null, scene, 'biped', 'hash-a')
    expect(infoA2.procedural).toBe(false)
    expect(gltfCacheStats().activeRefs).toBe(1)

    // 此时 Model B 也被卸载，但 Model B 的资源依然完好
    expect(bGeoSpy).not.toHaveBeenCalled()
    expect(bMatSpy).not.toHaveBeenCalled()

    // 4. 控制器销毁，归还 Model A 的引用
    controller.dispose()
    expect(gltfCacheStats().activeRefs).toBe(0)
    // 模板缓存仍持有 Model A 与 Model B，因此依然未 dispose
    expect(aGeoSpy).not.toHaveBeenCalled()
    expect(bGeoSpy).not.toHaveBeenCalled()

    // 5. 清理模板缓存时，正式释放所有 GPU 资源
    clearAllGltf()
    expect(aGeoSpy).toHaveBeenCalledTimes(1)
    expect(aMatSpy).toHaveBeenCalledTimes(1)
    expect(aTexSpy).toHaveBeenCalledTimes(1)
    expect(bGeoSpy).toHaveBeenCalledTimes(1)
    expect(bMatSpy).toHaveBeenCalledTimes(1)
  })

  it('程序化形象在卸载时正常释放独占资源', async () => {
    // 触发程序化形象兜底（bytes 为 null 且未命中缓存）
    const info = await controller.load(null, scene, 'biped')
    expect(info.procedural).toBe(true)

    const procBodyMesh = controller.root.children.find(c => c instanceof THREE.Mesh) as THREE.Mesh
    expect(procBodyMesh).toBeDefined()
    const geoSpy = vi.spyOn(procBodyMesh.geometry, 'dispose')
    const matSpy = vi.spyOn(procBodyMesh.material as THREE.Material, 'dispose')

    controller.dispose()
    expect(geoSpy).toHaveBeenCalled()
    expect(matSpy).toHaveBeenCalled()
  })
})
