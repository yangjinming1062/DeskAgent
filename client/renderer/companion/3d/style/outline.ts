import * as THREE from 'three'
import { cameraProjectionMatrix, float, modelViewMatrix, normalize, normalLocal, positionLocal, vec4 } from 'three/tsl'
import { NodeMaterial } from 'three/webgpu'

import type { SharedStyleUniforms } from './toon-materials'

/** Node-tier outline hull material — vendored from three's official
 * ToonOutlinePassNode._createMaterial (src/nodes/display/ToonOutlinePassNode.js):
 * clip-space constant-width offset via `norm * thickness * pos.w`. We reuse
 * the material math but attach per-mesh hull clones instead of the pass's
 * renderObjectFunction interception, so Engine.tick's plain render() call
 * stays untouched (no post-processing pipeline dependency). */
export function createNodeOutlineMaterial(u: SharedStyleUniforms): NodeMaterial {
  const material = new NodeMaterial()
  material.name = 'SpiritOutline'
  material.side = THREE.BackSide

  const outlineNormal = normalLocal.negate()
  const mvp = cameraProjectionMatrix.mul(modelViewMatrix)
  // NOTE: the BackSide objectNormal is negative, hence pos − pos2.
  const pos = mvp.mul(vec4(positionLocal, 1.0))
  const pos2 = mvp.mul(vec4(positionLocal.add(outlineNormal), 1.0))
  const norm = normalize(pos.sub(pos2))

  material.vertexNode = pos.add(norm.mul(u.outlineThickness.node).mul(pos.w))
  material.colorNode = vec4(u.outlineColor.node, float(1.0))

  return material
}

/** Classic-tier hull: MeshBasicMaterial + vertex injection. The injection
 * point sits right after <skinning_vertex>, where `objectNormal` is morph +
 * skin transformed and `transformed` is already skinned — the offset then
 * tracks animation. Non-skinned meshes fall back to the raw `normal`
 * attribute (unconditionally declared by WebGLProgram). */
export function createClassicOutlineMaterial(u: SharedStyleUniforms): THREE.MeshBasicMaterial {
  const material = new THREE.MeshBasicMaterial({ color: 0x111111, side: THREE.BackSide })

  material.onBeforeCompile = shader => {
    shader.uniforms.uOutlineThickness = u.outlineThickness.classic
    shader.vertexShader = shader.vertexShader
      .replace('void main() {', 'uniform float uOutlineThickness;\nvoid main() {')
      .replace(
        '#include <skinning_vertex>',
        [
          '#include <skinning_vertex>',
          '#ifdef USE_SKINNING',
          '\ttransformed += normalize( objectNormal ) * uOutlineThickness;',
          '#else',
          '\ttransformed += normal * uOutlineThickness;',
          '#endif'
        ].join('\n')
      )
  }

  material.customProgramCacheKey = (): string => 'spirit-outline'

  return material
}

export const OUTLINE_SUFFIX = '__outline'

/** Clone a mesh into its outline hull. Shares geometry (and, for
 * SkinnedMesh.clone, the skeleton reference) so skinning and morphs drive
 * both; Mesh.copy gives the hull its own morphTargetInfluences array, which
 * MorphController drives once the hull is mounted before discover(). */
export function buildOutlineHull(mesh: THREE.Mesh, material: THREE.Material): THREE.Mesh {
  const hull = mesh.clone() as THREE.Mesh
  hull.name = `${mesh.name}${OUTLINE_SUFFIX}`
  hull.material = material
  hull.renderOrder = -1
  hull.castShadow = false
  hull.receiveShadow = false
  // The hull inherits the source's frustumCulled bounds; geometry is shared
  // so the bounding box stays valid.

  mesh.add(hull)

  return hull
}
