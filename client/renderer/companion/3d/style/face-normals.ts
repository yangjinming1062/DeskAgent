import * as THREE from 'three'

import { log } from '@/shared/lib/log'

import { OUTLINE_SUFFIX } from './outline'

// Genshin-style face lighting: the face region lights against smoothed,
// mostly forward-facing normals so mesh micro-bumps (Tripo reconstructions
// are never clean) can't paint stray shadow patches across the face. The
// baked attribute replaces `normal` only while the toon style is active.

export interface FaceBakeOptions {
  /** Weight of the neck bone's skin influence (head gets 1.0). */
  neckWeight: number
  /** Radius multiplier over the head↔neck bone distance. */
  radiusScale: number
  /** Smoothing iterations over the accumulated vertex normals. */
  iterations: number
  /** Skip baking above this total vertex count (perf guard). */
  maxVertices: number
}

const DEFAULTS: FaceBakeOptions = {
  neckWeight: 0.35,
  radiusScale: 1.15,
  iterations: 2,
  maxVertices: 200_000
}

const NPR_MARKER = 'spiritNpr'

interface BakedGeometry {
  original: THREE.BufferAttribute
  baked: THREE.BufferAttribute
}

function normalizeInPlace(arr: Float32Array, count: number): void {
  for (let i = 0; i < count; i++) {
    const x = arr[i * 3]
    const y = arr[i * 3 + 1]
    const z = arr[i * 3 + 2]
    const len = Math.hypot(x, y, z)

    if (len > 1e-10) {
      arr[i * 3] = x / len
      arr[i * 3 + 1] = y / len
      arr[i * 3 + 2] = z / len
    }
  }
}

/** Area-weighted face-normal accumulation followed by neighbor averaging.
 * Each pass re-averages per-face over shared vertices, rounding the single
 * accumulation pass without flattening it entirely. */
function smoothNormals(geometry: THREE.BufferGeometry, iterations: number): Float32Array {
  const position = geometry.getAttribute('position') as THREE.BufferAttribute
  const count = position.count
  const index = geometry.index ? (geometry.index.array as Uint32Array | Uint16Array) : null
  const accum = new Float32Array(count * 3)

  const vA = new THREE.Vector3()
  const vB = new THREE.Vector3()
  const vC = new THREE.Vector3()
  const cb = new THREE.Vector3()
  const ab = new THREE.Vector3()
  const faceNormal = new THREE.Vector3()

  const forEachFace = (visit: (a: number, b: number, c: number) => void): void => {
    if (index) {
      for (let i = 0; i < index.length; i += 3) {
        visit(index[i], index[i + 1], index[i + 2])
      }
    } else {
      for (let i = 0; i + 2 < count; i += 3) {
        visit(i, i + 1, i + 2)
      }
    }
  }

  // Pass 0: accumulate area-weighted face normals (cross-product magnitude
  // is 2× the triangle area, so no explicit weight is needed).
  forEachFace((a, b, c) => {
    vA.fromBufferAttribute(position, a)
    vB.fromBufferAttribute(position, b)
    vC.fromBufferAttribute(position, c)
    cb.subVectors(vC, vB)
    ab.subVectors(vA, vB)
    faceNormal.crossVectors(cb, ab)

    for (const v of [a, b, c]) {
      accum[v * 3 + 0] += faceNormal.x
      accum[v * 3 + 1] += faceNormal.y
      accum[v * 3 + 2] += faceNormal.z
    }
  })

  normalizeInPlace(accum, count)

  for (let iter = 0; iter < Math.max(0, iterations - 1); iter++) {
    const next = new Float32Array(accum)

    forEachFace((a, b, c) => {
      const nx = accum[a * 3] + accum[b * 3] + accum[c * 3]
      const ny = accum[a * 3 + 1] + accum[b * 3 + 1] + accum[c * 3 + 1]
      const nz = accum[a * 3 + 2] + accum[b * 3 + 2] + accum[c * 3 + 2]

      for (const v of [a, b, c]) {
        next[v * 3 + 0] += nx
        next[v * 3 + 1] += ny
        next[v * 3 + 2] += nz
      }
    })

    normalizeInPlace(next, count)
    accum.set(next)
  }

  return accum
}

/** Per-vertex face-region weight: max(skin-weight signal, head-bone radius
 * falloff). Either signal alone mislabels — skinning bleeds at the jawline,
 * and a radius catches hats/hair we deliberately also want smoothed. */
function vertexWeights(
  mesh: THREE.Mesh,
  headBone: THREE.Bone | null,
  neckBone: THREE.Bone | null,
  opts: FaceBakeOptions
): Float32Array | null {
  if (!headBone) {
    return null
  }

  const position = mesh.geometry.getAttribute('position') as THREE.BufferAttribute | null

  if (!position) {
    return null
  }

  const count = position.count
  const weights = new Float32Array(count)

  let headIdx = -1
  let neckIdx = -1
  const skinned = mesh instanceof THREE.SkinnedMesh ? mesh : null
  const skinIndex = skinned?.geometry.getAttribute('skinIndex')
  const skinWeight = skinned?.geometry.getAttribute('skinWeight')

  if (skinned?.skeleton && skinIndex && skinWeight) {
    headIdx = skinned.skeleton.bones.indexOf(headBone)
    neckBone && (neckIdx = skinned.skeleton.bones.indexOf(neckBone))
  }

  const headWorld = headBone.getWorldPosition(new THREE.Vector3())
  const neckWorld = neckBone?.getWorldPosition(new THREE.Vector3()) ?? null
  const radius = neckWorld ? headWorld.distanceTo(neckWorld) * opts.radiusScale : 0.24

  const v = new THREE.Vector3()

  for (let i = 0; i < count; i++) {
    let w = 0

    if (headIdx >= 0 && skinIndex && skinWeight) {
      let skinSignal = 0

      for (let j = 0; j < 4; j++) {
        const boneIdx = skinIndex.getComponent(i, j)
        const weight = skinWeight.getComponent(i, j)

        if (boneIdx === headIdx) {
          skinSignal += weight
        } else if (neckIdx >= 0 && boneIdx === neckIdx) {
          skinSignal += weight * opts.neckWeight
        }
      }

      w = Math.max(w, skinSignal)
    }

    v.fromBufferAttribute(position, i).applyMatrix4(mesh.matrixWorld)
    const d = v.distanceTo(headWorld)
    const falloff = 1 - THREE.MathUtils.clamp((d - radius * 0.55) / (radius * 0.45), 0, 1)

    weights[i] = THREE.MathUtils.clamp(Math.max(w, falloff * 0.85), 0, 1)
  }

  return weights
}

/** Bake smoothed facial normals for every mesh under `root`. Idempotent —
 * re-baking derives from the stored original attribute, never from a
 * previous bake (gltf-instance-cache shares geometry between loads). Must
 * run at rest pose (before the first mixer update) so bone world positions
 * mean something. */
export function bakeFacialNormals(
  root: THREE.Object3D,
  headBone: THREE.Bone | null,
  neckBone: THREE.Bone | null,
  opts?: Partial<FaceBakeOptions>
): void {
  const o = { ...DEFAULTS, ...opts }

  root.updateMatrixWorld(true)

  let total = 0

  root.traverse(child => {
    if (child instanceof THREE.Mesh && !isHull(child) && child.geometry.getAttribute('position')) {
      total += (child.geometry.getAttribute('position') as THREE.BufferAttribute).count
    }
  })

  if (total > o.maxVertices) {
    log.warn('face-normals', `skipping bake: ${total} vertices over cap ${o.maxVertices}`)

    return
  }

  root.traverse(child => {
    if (!(child instanceof THREE.Mesh) || isHull(child)) {
      return
    }

    const geometry = child.geometry
    const normal = geometry.getAttribute('normal') as THREE.BufferAttribute | null

    if (!geometry.getAttribute('position') || !normal) {
      return
    }

    const weights = vertexWeights(child, headBone, neckBone, o)

    if (!weights) {
      return
    }

    let hasFace = false

    for (let i = 0; i < weights.length; i++) {
      if (weights[i] > 0.01) {
        hasFace = true

        break
      }
    }

    if (!hasFace) {
      return
    }

    const smoothed = smoothNormals(geometry, o.iterations)
    const blended = new Float32Array(smoothed.length)
    const originalArray = normal.array as ArrayLike<number>
    const count = (geometry.getAttribute('position') as THREE.BufferAttribute).count

    for (let i = 0; i < count; i++) {
      const w = weights[i]

      for (let c = 0; c < 3; c++) {
        blended[i * 3 + c] = originalArray[i * 3 + c] * (1 - w) + smoothed[i * 3 + c] * w
      }
    }

    normalizeInPlace(blended, count)

    const marker = geometry.userData[NPR_MARKER] as BakedGeometry | undefined

    geometry.userData[NPR_MARKER] = {
      original: marker?.original ?? (normal.clone() as THREE.BufferAttribute),
      baked: new THREE.BufferAttribute(blended, 3)
    }
  })
}

/** Swap the normal attribute between baked (toon) and original (PBR). */
export function setFacialNormals(root: THREE.Object3D, mode: 'toon' | 'original'): void {
  root.traverse(child => {
    if (!(child instanceof THREE.Mesh) || isHull(child)) {
      return
    }

    const marker = child.geometry.userData[NPR_MARKER] as BakedGeometry | undefined

    if (!marker) {
      return
    }

    child.geometry.setAttribute('normal', mode === 'toon' ? marker.baked : marker.original)
  })
}

function isHull(obj: THREE.Object3D): boolean {
  return obj.name.endsWith(OUTLINE_SUFFIX)
}
