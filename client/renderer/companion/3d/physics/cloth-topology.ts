// 布料网格拓扑提取 —— CPU（ClothSolver）与 GPU（TslComputeBackend）物理后端共用的唯一事实源。
// 两侧必须就锚点环、约束边和碰撞半径达成一致，否则 WebGL 降级路径的悬垂形态会与 WebGPU 路径不同。

export const ANCHOR_RATIO = 0.3 // 单元高度顶部区域固定到骨骼
export const MAX_VERTICES = 16384 // 性能护栏 —— 任一后端超出此值都拒绝
export const GRAVITY = -4.0 // 世界空间 m/s²
export const DAMPING = 0.97 // Verlet 每帧速度保持率（1 表示无阻尼）
export const ITERATIONS = 3 // 每帧距离约束松弛次数
export const SKIN_CLEARANCE = 0.002 // skin 单元相对身体表面的推出余量（米）

// 用后缀匹配的骨骼（mixamorig: 前缀不参与匹配），其世界位置成为碰撞球。
// 躯干 + 腿部覆盖人形的裙子 / 下垂物碰撞；其他骨骼类型就以命中的骨骼为准。
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

/** pinAll 为真时把每个顶点都钉到它的蒙皮目标；否则取单元顶部高度 `ratio` 比例的带状区域作为锚点环。 */
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

/** 根据 index buffer 提取唯一的结构边与静态长度；网格没有索引或超过顶点上限时返回 null。 */
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
  /** 长度 vertCount + 1 —— 顶点 v 的三角形引用位于 list[offsets[v], offsets[v + 1]) 区间。 */
  offsets: Uint32Array
  list: Uint32Array
}

/** 按顶点构建三角形邻接，用于并行的法线累加：每个顶点只写自己的法线，
 * 因此不需要原子操作（WGSL 原子只支持 i32 / u32），零面积叉积也不会污染其他顶点。 */
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
