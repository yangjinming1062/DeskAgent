import * as THREE from 'three'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  clearAllGltf,
  clearGltf,
  disposeThreeResources,
  gltfCacheStats,
  hasGltf,
  releaseGltf,
  stashGltf,
  takeGltfClone
} from './gltf-instance-cache'

function createSampleSkinnedModel(): {
  animations: THREE.AnimationClip[]
  boneChild: THREE.Bone
  boneRoot: THREE.Bone
  geometry: THREE.BufferGeometry
  material: THREE.MeshStandardMaterial
  mesh: THREE.SkinnedMesh
  root: THREE.Group
  texture: THREE.Texture
} {
  const root = new THREE.Group()
  const boneRoot = new THREE.Bone()
  boneRoot.name = 'RootBone'
  const boneChild = new THREE.Bone()
  boneChild.name = 'ChildBone'
  boneRoot.add(boneChild)
  root.add(boneRoot)

  const geometry = new THREE.BufferGeometry()
  const texture = new THREE.Texture()
  const material = new THREE.MeshStandardMaterial({ map: texture })
  const mesh = new THREE.SkinnedMesh(geometry, material)
  const skeleton = new THREE.Skeleton([boneRoot, boneChild])
  mesh.bind(skeleton)
  root.add(mesh)

  const clip = new THREE.AnimationClip('idle', 1, [
    new THREE.VectorKeyframeTrack('RootBone.position', [0, 1], [0, 0, 0, 0, 1, 0])
  ])

  return {
    animations: [clip],
    boneChild,
    boneRoot,
    geometry,
    material,
    mesh,
    root,
    texture
  }
}

describe('gltf-instance-cache', () => {
  beforeEach(() => {
    clearAllGltf(true)
  })

  it('使用 SkeletonUtils.clone 重建骨骼并重新绑定 SkinnedMesh 到新骨骼树', () => {
    const { animations, boneChild, boneRoot, geometry, material, root } = createSampleSkinnedModel()
    stashGltf('model-1', root, animations, 1024)

    const cloned = takeGltfClone('model-1')
    expect(cloned).not.toBeNull()

    if (!cloned) {
      return
    }

    expect(cloned.scene).not.toBe(root)

    const clonedMesh = cloned.scene.children.find(c => c instanceof THREE.SkinnedMesh) as THREE.SkinnedMesh
    const clonedBoneRoot = cloned.scene.children.find(c => c instanceof THREE.Bone) as THREE.Bone
    const clonedBoneChild = clonedBoneRoot.children.find(c => c instanceof THREE.Bone) as THREE.Bone

    expect(clonedMesh).toBeDefined()
    expect(clonedBoneRoot).toBeDefined()
    expect(clonedBoneChild).toBeDefined()

    // 骨骼实例已重新创建，不指向原模板骨骼
    expect(clonedBoneRoot).not.toBe(boneRoot)
    expect(clonedBoneChild).not.toBe(boneChild)
    expect(clonedMesh.skeleton.bones[0]).toBe(clonedBoneRoot)
    expect(clonedMesh.skeleton.bones[1]).toBe(clonedBoneChild)
    expect(clonedMesh.skeleton.bones[0]).not.toBe(boneRoot)

    // 几何体与材质被克隆体与模板共享（只读）
    expect(clonedMesh.geometry).toBe(geometry)
    expect(clonedMesh.material).toBe(material)
  })

  it('克隆出的 AnimationClip 实例与模板隔离，修改克隆不影响模板', () => {
    const { animations, root } = createSampleSkinnedModel()
    stashGltf('model-anim', root, animations, 1024)

    const cloned = takeGltfClone('model-anim')
    expect(cloned).not.toBeNull()

    if (!cloned) {
      return
    }

    expect(cloned.animations[0]).not.toBe(animations[0])
    expect(cloned.animations[0].name).toBe('idle')

    // 修改克隆动画的持续时间与轨道
    cloned.animations[0].duration = 99
    expect(animations[0].duration).toBe(1)
  })

  it('disposeThreeResources 正确递归释放 geometry, material 数组及 PBR 贴图，且不重复释放共享贴图', () => {
    const root = new THREE.Group()
    const geo = new THREE.BufferGeometry()
    const texMap = new THREE.Texture()
    const texNormal = new THREE.Texture()

    const geoDisposeSpy = vi.spyOn(geo, 'dispose')
    const texMapDisposeSpy = vi.spyOn(texMap, 'dispose')
    const texNormalDisposeSpy = vi.spyOn(texNormal, 'dispose')

    // 两个材质共享同一个 texMap
    const mat1 = new THREE.MeshStandardMaterial({ map: texMap, normalMap: texNormal })
    const mat2 = new THREE.MeshStandardMaterial({ map: texMap })
    const mat1DisposeSpy = vi.spyOn(mat1, 'dispose')
    const mat2DisposeSpy = vi.spyOn(mat2, 'dispose')

    const mesh1 = new THREE.Mesh(geo, mat1)
    const mesh2 = new THREE.Mesh(geo, [mat1, mat2])
    root.add(mesh1)
    root.add(mesh2)

    disposeThreeResources(root)

    expect(geoDisposeSpy).toHaveBeenCalledTimes(1)
    expect(mat1DisposeSpy).toHaveBeenCalledTimes(1)
    expect(mat2DisposeSpy).toHaveBeenCalledTimes(1)
    expect(texMapDisposeSpy).toHaveBeenCalledTimes(1)
    expect(texNormalDisposeSpy).toHaveBeenCalledTimes(1)
  })

  it('模板引用计数生命周期：活跃引用存在时 clear 延迟释放，归还引用后触发实际销毁', () => {
    const { animations, geometry, material, root, texture } = createSampleSkinnedModel()
    const geoSpy = vi.spyOn(geometry, 'dispose')
    const matSpy = vi.spyOn(material, 'dispose')
    const texSpy = vi.spyOn(texture, 'dispose')

    stashGltf('ref-test', root, animations, 2048)
    expect(hasGltf('ref-test')).toBe(true)

    // 取出克隆，引用计数为 1
    const clone = takeGltfClone('ref-test')
    expect(clone).not.toBeNull()
    const stats1 = gltfCacheStats()
    expect(stats1.activeRefs).toBe(1)

    // 活跃引用存在时调用 clearGltf
    clearGltf('ref-test')
    // 从缓存表中移除，后续无法再命中
    expect(hasGltf('ref-test')).toBe(false)
    // 但底层 GPU 资源尚未被销毁（克隆体正在使用）
    expect(geoSpy).not.toHaveBeenCalled()
    expect(matSpy).not.toHaveBeenCalled()
    expect(texSpy).not.toHaveBeenCalled()

    // 实例释放归还引用 -> 触发延迟销毁
    releaseGltf('ref-test')
    expect(geoSpy).toHaveBeenCalledTimes(1)
    expect(matSpy).toHaveBeenCalledTimes(1)
    expect(texSpy).toHaveBeenCalledTimes(1)
  })

  it('LRU 容量限制与淘汰策略：优先淘汰 refCount === 0 的最久未使用模板', () => {
    const m1 = createSampleSkinnedModel()
    const m2 = createSampleSkinnedModel()
    const m3 = createSampleSkinnedModel()
    const m4 = createSampleSkinnedModel()

    const m1GeoSpy = vi.spyOn(m1.geometry, 'dispose')
    const m2GeoSpy = vi.spyOn(m2.geometry, 'dispose')

    // maxTemplates 设为 2
    stashGltf('k1', m1.root, m1.animations, 100, 2, 1000)
    stashGltf('k2', m2.root, m2.animations, 100, 2, 1000)

    // 持有 k2 的活跃引用
    const c2 = takeGltfClone('k2')
    expect(c2).not.toBeNull()

    // 插入 k3，超出条目上限 2：k1 引用为 0，k2 引用为 1，应淘汰 k1
    stashGltf('k3', m3.root, m3.animations, 100, 2, 1000)
    expect(hasGltf('k1')).toBe(false)
    expect(m1GeoSpy).toHaveBeenCalledTimes(1)
    expect(hasGltf('k2')).toBe(true)
    expect(hasGltf('k3')).toBe(true)

    // 释放 k2
    releaseGltf('k2')
    // 插入 k4，淘汰 k2
    stashGltf('k4', m4.root, m4.animations, 100, 2, 1000)
    expect(hasGltf('k2')).toBe(false)
    expect(m2GeoSpy).toHaveBeenCalledTimes(1)
    expect(hasGltf('k3')).toBe(true)
    expect(hasGltf('k4')).toBe(true)
  })
})
