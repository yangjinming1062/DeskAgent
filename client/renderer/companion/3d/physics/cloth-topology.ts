// Cloth mesh topology extraction — the single source of truth shared by the
// CPU (ClothSolver) and GPU (TslComputeBackend) physics backends. Both must
// agree on anchor rings, constraint edges and collider radii, or the WebGL
// fallback would drape differently from the WebGPU path.

export const ANCHOR_RATIO = 0.3 // top band of the unit's height pinned to the skeleton
export const MAX_VERTICES = 16384 // perf guard — both backends decline above this
export const GRAVITY = -4.0 // world-space m/s²
export const DAMPING = 0.97 // verlet velocity retention per frame (1 = no damping)
export const ITERATIONS = 3 // distance-constraint relaxation passes per frame
export const SKIN_CLEARANCE = 0.002 // skin-unit push-out clearance (m) from the body surface

// Bones (matched by suffix so mixamorig: prefixes don't matter) whose world
// positions become collision spheres. Torso + legs cover skirt/drape collision
// for bipeds; other rigs simply collide with whichever bones match.
export const COLLIDER_RADII: Record<string, number> = {
  Hips: 0.15,
  Spine: 0.14,
  Spine1: 0.14,
  Spine2: 0.14,
  Neck: 0.06,
  Head: 0.11,
  LeftUpLeg: 0.09,
  RightUpLeg: 0.09,
  LeftLeg: 0.08,
  RightLeg: 0.08
}

/** pinAll pins every vertex to its skinned target; otherwise the top `ratio`
 * band of the unit's height forms the anchor ring. */
export function buildAnchors(
  base: Float32Array,
  count: number,
  pinAll: boolean,
  ratio: number = ANCHOR_RATIO
): Uint8Array {
  if (pinAll) {
    return new Uint8Array(count).fill(1)
  }

  let minY = Infinity
  let maxY = -Infinity

  for (let i = 1; i < base.length; i += 3) {
    if (base[i] < minY) {
      minY = base[i]
    }

    if (base[i] > maxY) {
      maxY = base[i]
    }
  }

  const threshold = minY + (maxY - minY) * ratio
  const anchors = new Uint8Array(count)

  for (let i = 0; i < count; i++) {
    if (base[i * 3 + 1] >= threshold) {
      anchors[i] = 1
    }
  }

  return anchors
}

export interface ClothConstraints {
  edges: Uint32Array
  rest: Float32Array
}

/** Unique structural edges + rest lengths from the index buffer; null when
 * the mesh has no index buffer or exceeds the vertex budget. */
export function buildConstraints(index: ArrayLike<number>, count: number, base: Float32Array): ClothConstraints | null {
  if (count > MAX_VERTICES) {
    return null
  }

  const seen = new Set<number>()
  const pairs: number[] = []

  for (let t = 0; t < index.length; t += 3) {
    for (const [a, b] of [
      [index[t], index[t + 1]],
      [index[t + 1], index[t + 2]],
      [index[t + 2], index[t]]
    ]) {
      const key = a < b ? a * count + b : b * count + a

      if (!seen.has(key)) {
        seen.add(key)
        pairs.push(a, b)
      }
    }
  }

  const edges = new Uint32Array(pairs)
  const rest = new Float32Array(pairs.length / 2)

  for (let e = 0; e < pairs.length; e += 2) {
    const a = pairs[e] * 3
    const b = pairs[e + 1] * 3

    rest[e / 2] = Math.hypot(base[a] - base[b], base[a + 1] - base[b + 1], base[a + 2] - base[b + 2])
  }

  return { edges, rest }
}

export interface VertexTriAdjacency {
  /** Length vertCount + 1 — triangle refs for vertex v live in list[offsets[v], offsets[v + 1]). */
  offsets: Uint32Array
  list: Uint32Array
}

/** Per-vertex triangle adjacency for parallel normal accumulation: each vertex
 * only writes its own normal, so no atomics (WGSL atomics are i32/u32 only)
 * and no zero-area cross-product can poison another vertex. */
export function buildVertexTriAdjacency(index: ArrayLike<number>, vertCount: number): VertexTriAdjacency {
  const offsets = new Uint32Array(vertCount + 1)

  for (let i = 0; i < index.length; i++) {
    offsets[index[i] + 1]++
  }

  for (let v = 0; v < vertCount; v++) {
    offsets[v + 1] += offsets[v]
  }

  const list = new Uint32Array(index.length)
  const cursor = offsets.slice(0, vertCount)

  for (let t = 0; t < index.length / 3; t++) {
    for (let k = 0; k < 3; k++) {
      const v = index[t * 3 + k]
      list[cursor[v]++] = t
    }
  }

  return { offsets, list }
}
