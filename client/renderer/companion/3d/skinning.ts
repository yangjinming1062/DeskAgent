import type * as THREE from 'three'

/**
 * CPU linear-blend skin of a single vertex — the exact CPU equivalent of the
 * GPU `skinning_vertex` transform:
 *
 *   skinned = Σ wᵢ · boneMatricesᵢ · (bindMatrix · position)
 *
 * `boneMatrices` is the flattened `skeleton.boneMatrices` buffer (16 floats
 * per bone, column-major, mapping bind space → world space). `bindMatrix`
 * maps mesh local space → bind space and MUST be applied before the weights;
 * omitting it is only valid when it is identity (glTF exported at origin),
 * which is the assumption this helper exists to remove.
 *
 * `skinIndex` / `skinWeight` are the per-vertex flat attributes with
 * `itemSize` entries per vertex; `vo` is the vertex's first-influence offset
 * (`vertexIndex * itemSize`). Only the first four influences are used, which
 * matches the glTF default and the garment pipeline's four-bone weight cap.
 */
export function cpuSkinPoint(
  x: number,
  y: number,
  z: number,
  skinIndex: ArrayLike<number>,
  skinWeight: ArrayLike<number>,
  vo: number,
  boneMatrices: ArrayLike<number>,
  bindMatrix: THREE.Matrix4 | null,
  out: THREE.Vector3
): THREE.Vector3 {
  const e = bindMatrix?.elements
  let bx = x
  let by = y
  let bz = z

  if (e) {
    bx = e[0] * x + e[4] * y + e[8] * z + e[12]
    by = e[1] * x + e[5] * y + e[9] * z + e[13]
    bz = e[2] * x + e[6] * y + e[10] * z + e[14]
  }

  let sx = 0
  let sy = 0
  let sz = 0

  for (let j = 0; j < 4; j++) {
    const w = skinWeight[vo + j]

    if (w === 0) {
      continue
    }

    const off = skinIndex[vo + j] * 16
    sx +=
      w * (boneMatrices[off] * bx + boneMatrices[off + 4] * by + boneMatrices[off + 8] * bz + boneMatrices[off + 12])
    sy +=
      w *
      (boneMatrices[off + 1] * bx + boneMatrices[off + 5] * by + boneMatrices[off + 9] * bz + boneMatrices[off + 13])
    sz +=
      w *
      (boneMatrices[off + 2] * bx + boneMatrices[off + 6] * by + boneMatrices[off + 10] * bz + boneMatrices[off + 14])
  }

  return out.set(sx, sy, sz)
}
