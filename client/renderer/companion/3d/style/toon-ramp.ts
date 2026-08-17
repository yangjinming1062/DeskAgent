import * as THREE from 'three'

/** Build the 1D toon ramp texture sampled as `vec2(dotNL * 0.5 + 0.5, 0)`.
 * NearestFilter + NoColorSpace are hard requirements of MeshToonMaterial's
 * gradient sampling — any filtering would blur the cel bands. */
export function createToonRampTexture(steps: readonly number[]): THREE.DataTexture {
  const data = new Uint8Array(steps.length * 4)

  for (let i = 0; i < steps.length; i++) {
    const v = Math.round(THREE.MathUtils.clamp(steps[i], 0, 1) * 255)
    data[i * 4 + 0] = v
    data[i * 4 + 1] = v
    data[i * 4 + 2] = v
    data[i * 4 + 3] = 255
  }

  const tex = new THREE.DataTexture(data, steps.length, 1, THREE.RGBAFormat, THREE.UnsignedByteType)
  tex.minFilter = THREE.NearestFilter
  tex.magFilter = THREE.NearestFilter
  tex.wrapS = THREE.ClampToEdgeWrapping
  tex.generateMipmaps = false
  tex.flipY = false
  tex.colorSpace = THREE.NoColorSpace
  tex.needsUpdate = true

  return tex
}
