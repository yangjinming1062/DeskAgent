import * as THREE from 'three'

import { cpuSkinPoint } from './skinning'

/**
 * 动画化身体的运行时碰撞表面，用来防止 `physics=skin` 服装单元在动画中
 * 穿入身体。
 *
 * 身体网格由 GPU 蒙皮，其动画表面无法直接从 `geometry.attributes.position`
 * 读取（顶点保持在绑定姿态下）。要在 CPU 上做碰撞，需先把身体精简为低面代理，
 * 每帧用同一骨架 CPU 蒙皮代理，再对蒙皮后的三角形构建中位数切分 BVH，
 * 用于最近点推离查询。
 *
 * 代理是基于顶点聚类的近似，曲面区域与真实身体可能相差几毫米。这是为消除
 * 明显的动画期穿模所做的可接受权衡；精确表面碰撞留待以后处理（见 docs/plan.md）。
 */

const MAX_PROXY_VERTS = 4096

interface ProxyMesh {
  positions: Float32Array
  skinIndex: Uint16Array
  skinWeight: Float32Array
  index: Uint32Array
}

const _closest = new THREE.Vector3()
const _normal = new THREE.Vector3()
const _tmp = new THREE.Vector3()

export class BodyCollider {
  private readonly skeleton: THREE.Skeleton
  private readonly bindMatrix: THREE.Matrix4 | null

  private readonly bindPos: Float32Array
  private readonly skinIndex: Uint16Array
  private readonly skinWeight: Float32Array
  private readonly index: Uint32Array
  private readonly world: Float32Array

  // 在代理三角形（世界空间）上构建中位数分割 BVH，每次 update() 重建。
  private bvhMin = new Float32Array(0)
  private bvhMax = new Float32Array(0)
  private bvhLeft = new Int32Array(0)
  private bvhRight = new Int32Array(0)
  private bvhTri = new Int32Array(0)
  private bvhRoot = -1

  constructor(body: THREE.SkinnedMesh) {
    const geo = body.geometry
    const pos = geo.getAttribute('position')
    const si = geo.getAttribute('skinIndex')
    const sw = geo.getAttribute('skinWeight')
    const idx = geo.getIndex()

    if (!pos || !si || !sw || !idx) {
      throw new Error('BodyCollider requires a skinned body mesh with position/skinIndex/skinWeight/index')
    }

    this.skeleton = body.skeleton
    this.bindMatrix = body.bindMatrix ?? null

    const proxy = buildProxy(
      pos.array as Float32Array,
      si.array as ArrayLike<number>,
      si.itemSize,
      sw.array as Float32Array,
      sw.itemSize,
      idx.array as ArrayLike<number>
    )

    this.bindPos = proxy.positions
    this.skinIndex = proxy.skinIndex
    this.skinWeight = proxy.skinWeight
    this.index = proxy.index
    this.world = new Float32Array(proxy.positions.length)
  }

  /** 把代理 CPU 蒙皮到世界空间并重建 BVH。每帧求解前调用一次。 */
  update(): void {
    if (
      !this.skeleton.boneMatrices ||
      (this.skeleton.boneMatrices[0] === 0 &&
        this.skeleton.boneMatrices[5] === 0 &&
        this.skeleton.boneMatrices[10] === 0)
    ) {
      this.skeleton.update()
    }

    const bm = this.skeleton.boneMatrices as Float32Array
    const vertCount = this.bindPos.length / 3

    for (let i = 0; i < vertCount; i++) {
      cpuSkinPoint(
        this.bindPos[i * 3],
        this.bindPos[i * 3 + 1],
        this.bindPos[i * 3 + 2],
        this.skinIndex,
        this.skinWeight,
        i * 4,
        bm,
        this.bindMatrix,
        _tmp
      )
      this.world[i * 3] = _tmp.x
      this.world[i * 3 + 1] = _tmp.y
      this.world[i * 3 + 2] = _tmp.z
    }

    this.buildBvh()
  }

  /**
   * 当 `point`（世界空间，原地变更）位于身体内部时，将其沿表面外推到 `clearance`
   * 距离。返回是否施加了推力。
   */
  resolve(point: THREE.Vector3, clearance: number): boolean {
    if (this.bvhRoot < 0) {
      return false
    }

    let bestDist2 = Infinity
    let bestTri = -1

    const stack = [this.bvhRoot]

    while (stack.length > 0) {
      const n = stack.pop()!

      if (aabbDist2(point.x, point.y, point.z, this.bvhMin, this.bvhMax, n) > bestDist2) {
        continue
      }

      const tri = this.bvhTri[n]

      if (tri >= 0) {
        const d2 = closestPointOnTri(point.x, point.y, point.z, tri, this.world, this.index, _closest, _normal)

        if (d2 < bestDist2) {
          bestDist2 = d2
          bestTri = tri
        }
      } else {
        stack.push(this.bvhRight[n], this.bvhLeft[n])
      }
    }

    if (bestTri < 0) {
      return false
    }

    closestPointOnTri(point.x, point.y, point.z, bestTri, this.world, this.index, _closest, _normal)

    _tmp.copy(point).sub(_closest)
    const d = _tmp.dot(_normal)

    if (d < clearance) {
      point.copy(_closest).addScaledVector(_normal, clearance)

      return true
    }

    return false
  }

  private buildBvh(): void {
    const triCount = this.index.length / 3

    if (triCount === 0) {
      this.bvhRoot = -1

      return
    }

    // 节点数组按最坏情况满二叉树（2·N − 1 个节点）预留容量。
    const maxNodes = triCount * 2
    this.bvhMin = new Float32Array(maxNodes * 3)
    this.bvhMax = new Float32Array(maxNodes * 3)
    this.bvhLeft = new Int32Array(maxNodes)
    this.bvhRight = new Int32Array(maxNodes)
    this.bvhTri = new Int32Array(maxNodes).fill(-1)

    // 每三角形的 AABB 与质心（世界空间）。
    const triMin = new Float32Array(triCount * 3)
    const triMax = new Float32Array(triCount * 3)
    const centroid = new Float32Array(triCount * 3)

    for (let t = 0; t < triCount; t++) {
      const a = this.index[t * 3] * 3
      const b = this.index[t * 3 + 1] * 3
      const c = this.index[t * 3 + 2] * 3

      for (let d = 0; d < 3; d++) {
        const mn = Math.min(this.world[a + d], this.world[b + d], this.world[c + d])
        const mx = Math.max(this.world[a + d], this.world[b + d], this.world[c + d])
        triMin[t * 3 + d] = mn
        triMax[t * 3 + d] = mx
        centroid[t * 3 + d] = (mn + mx) * 0.5
      }
    }

    const order = new Uint32Array(triCount)

    for (let t = 0; t < triCount; t++) {
      order[t] = t
    }

    let nextNode = 0

    const build = (start: number, end: number): number => {
      const node = nextNode++
      let mn = [Infinity, Infinity, Infinity]
      let mx = [-Infinity, -Infinity, -Infinity]

      for (let i = start; i < end; i++) {
        const t = order[i]

        for (let d = 0; d < 3; d++) {
          if (triMin[t * 3 + d] < mn[d]) {
            mn[d] = triMin[t * 3 + d]
          }

          if (triMax[t * 3 + d] > mx[d]) {
            mx[d] = triMax[t * 3 + d]
          }
        }
      }

      this.bvhMin[node * 3] = mn[0]
      this.bvhMin[node * 3 + 1] = mn[1]
      this.bvhMin[node * 3 + 2] = mn[2]
      this.bvhMax[node * 3] = mx[0]
      this.bvhMax[node * 3 + 1] = mx[1]
      this.bvhMax[node * 3 + 2] = mx[2]

      if (end - start === 1) {
        this.bvhLeft[node] = -1
        this.bvhRight[node] = -1
        this.bvhTri[node] = order[start]

        return node
      }

      const extent = [mx[0] - mn[0], mx[1] - mn[1], mx[2] - mn[2]]
      const axis = extent[0] >= extent[1] && extent[0] >= extent[2] ? 0 : extent[1] >= extent[2] ? 1 : 2

      // 原位 typed-array 快速排序——替换旧版 `Array.from(...).sort(...)`，
      // 后者在 4K 三角形代理上每帧分配约 50K 元素。
      sortByCentroidInPlace(order, start, end, centroid, axis)

      const mid = (start + end) >> 1
      this.bvhLeft[node] = build(start, mid)
      this.bvhRight[node] = build(mid, end)
      this.bvhTri[node] = -1

      return node
    }

    this.bvhRoot = build(0, triCount)
  }
}

/** 对 typed-array 的 `[start, end)` 切片做原地快速排序：中位数三路 + 三向 Dutch Flag 分区。 */
const INSERTION_SORT_THRESHOLD = 16

function sortByCentroidInPlace(
  order: Uint32Array,
  start: number,
  end: number,
  centroid: Float32Array,
  axis: number
): void {
  const length = end - start

  if (length <= 1) {
    return
  }

  // 小切片使用插入排序——常数因子小于快速排序，且无递归开销。
  if (length <= INSERTION_SORT_THRESHOLD) {
    for (let i = start + 1; i < end; i++) {
      const key = order[i]
      const keyVal = centroid[key * 3 + axis]
      let j = i - 1

      while (j >= start && centroid[order[j] * 3 + axis] > keyVal) {
        order[j + 1] = order[j]
        j--
      }

      order[j + 1] = key
    }

    return
  }

  // 三数取中枢纽：取 `start`、`mid`、`end-1` 三者的中位数。
  const mid = start + (length >> 1)
  const a = order[start]
  const b = order[mid]
  const c = order[end - 1]
  const ca = centroid[a * 3 + axis]
  const cb = centroid[b * 3 + axis]
  const cc = centroid[c * 3 + axis]

  const pivotTri =
    (ca <= cb && cb <= cc) || (cc <= cb && cb <= ca) ? b : (ca <= cc && cc <= cb) || (cb <= cc && cc <= ca) ? c : a

  const pivotVal = centroid[pivotTri * 3 + axis]

  // 3-way Dutch Flag partition: [start, lt) < pivot, [lt, gt) = pivot, [gt, end) > pivot — reduces recursion depth on duplicate centroids.
  let lt = start
  let i = start
  let gt = end

  while (i < gt) {
    const v = centroid[order[i] * 3 + axis]

    if (v < pivotVal) {
      const tmp = order[lt]
      order[lt] = order[i]
      order[i] = tmp
      lt++
      i++
    } else if (v > pivotVal) {
      gt--
      const tmp = order[i]
      order[i] = order[gt]
      order[gt] = tmp
    } else {
      i++
    }
  }

  sortByCentroidInPlace(order, start, lt, centroid, axis)
  sortByCentroidInPlace(order, gt, end, centroid, axis)
}

/** 点到节点 AABB 的距离平方（用于 BVH 剪枝）。 */
function aabbDist2(px: number, py: number, pz: number, min: Float32Array, max: Float32Array, node: number): number {
  let d2 = 0

  for (let d = 0; d < 3; d++) {
    const lo = min[node * 3 + d]
    const hi = max[node * 3 + d]
    const p = d === 0 ? px : d === 1 ? py : pz

    if (p < lo) {
      const q = lo - p
      d2 += q * q
    } else if (p > hi) {
      const q = p - hi
      d2 += q * q
    }
  }

  return d2
}

/**
 * 代理三角形 `tri` 上最接近 `(px,py,pz)` 的点。写入最近点与朝外法线
 * （代理绕序），返回距离平方。
 */
function closestPointOnTri(
  px: number,
  py: number,
  pz: number,
  tri: number,
  world: Float32Array,
  index: Uint32Array,
  out: THREE.Vector3,
  normal: THREE.Vector3
): number {
  const a = index[tri * 3] * 3
  const b = index[tri * 3 + 1] * 3
  const c = index[tri * 3 + 2] * 3

  const ax = world[a]
  const ay = world[a + 1]
  const az = world[a + 2]
  const bx = world[b]
  const by = world[b + 1]
  const bz = world[b + 2]
  const cx = world[c]
  const cy = world[c + 1]
  const cz = world[c + 2]

  // 朝外的面法线（CCW 缠绕），用于有符号推出方向。
  const v0x = bx - ax
  const v0y = by - ay
  const v0z = bz - az
  const v1x = cx - ax
  const v1y = cy - ay
  const v1z = cz - az
  const nx = v0y * v1z - v0z * v1y
  const ny = v0z * v1x - v0x * v1z
  const nz = v0x * v1y - v0y * v1x
  const nl = Math.hypot(nx, ny, nz)

  if (nl > 1e-12) {
    normal.set(nx / nl, ny / nl, nz / nl)
  }

  // Ericson 的三角形最近点算法，基于重心坐标区域判定。
  const v2x = px - ax
  const v2y = py - ay
  const v2z = pz - az
  const d00 = v0x * v0x + v0y * v0y + v0z * v0z
  const d01 = v0x * v1x + v0y * v1y + v0z * v1z
  const d11 = v1x * v1x + v1y * v1y + v1z * v1z
  const d20 = v2x * v0x + v2y * v0y + v2z * v0z
  const d21 = v2x * v1x + v2y * v1y + v2z * v1z
  const denom = d00 * d11 - d01 * d01

  if (denom > 1e-12) {
    const v = (d11 * d20 - d01 * d21) / denom
    const w = (d00 * d21 - d01 * d20) / denom

    if (v >= 0 && w >= 0 && v + w <= 1) {
      out.set(ax + v * v0x + w * v1x, ay + v * v0y + w * v1y, az + v * v0z + w * v1z)

      const dx = px - out.x
      const dy = py - out.y
      const dz = pz - out.z

      return dx * dx + dy * dy + dz * dz
    }
  }

  return closestPointOnTriEdges(px, py, pz, ax, ay, az, bx, by, bz, cx, cy, cz, out)
}

function closestPointOnTriEdges(
  px: number,
  py: number,
  pz: number,
  ax: number,
  ay: number,
  az: number,
  bx: number,
  by: number,
  bz: number,
  cx: number,
  cy: number,
  cz: number,
  out: THREE.Vector3
): number {
  // 取三条边上的最近点。
  let best = closestPointOnSegment(px, py, pz, ax, ay, az, bx, by, bz)
  let rx = best[0]
  let ry = best[1]
  let rz = best[2]
  let bestD2 = best[3]

  const e2 = closestPointOnSegment(px, py, pz, bx, by, bz, cx, cy, cz)

  if (e2[3] < bestD2) {
    rx = e2[0]
    ry = e2[1]
    rz = e2[2]
    bestD2 = e2[3]
  }

  const e3 = closestPointOnSegment(px, py, pz, cx, cy, cz, ax, ay, az)

  if (e3[3] < bestD2) {
    rx = e3[0]
    ry = e3[1]
    rz = e3[2]
    bestD2 = e3[3]
  }

  out.set(rx, ry, rz)

  return bestD2
}

/** 线段 ab 上最接近 p 的点。返回 [x, y, z, 距离平方]。 */
function closestPointOnSegment(
  px: number,
  py: number,
  pz: number,
  ax: number,
  ay: number,
  az: number,
  bx: number,
  by: number,
  bz: number
): [number, number, number, number] {
  const abx = bx - ax
  const aby = by - ay
  const abz = bz - az
  const apx = px - ax
  const apy = py - ay
  const apz = pz - az
  const ab2 = abx * abx + aby * aby + abz * abz

  let t = 0

  if (ab2 > 1e-12) {
    t = (apx * abx + apy * aby + apz * abz) / ab2
    t = t < 0 ? 0 : t > 1 ? 1 : t
  }

  const x = ax + t * abx
  const y = ay + t * aby
  const z = az + t * abz
  const dx = px - x
  const dy = py - y
  const dz = pz - z

  return [x, y, z, dx * dx + dy * dy + dz * dz]
}

/** 基于顶点聚类构建身体几何的代理。 */
function buildProxy(
  pos: Float32Array,
  si: ArrayLike<number>,
  siSize: number,
  sw: Float32Array,
  swSize: number,
  idx: ArrayLike<number>
): ProxyMesh {
  const vertCount = pos.length / 3

  let minX = Infinity
  let minY = Infinity
  let minZ = Infinity
  let maxX = -Infinity
  let maxY = -Infinity
  let maxZ = -Infinity

  for (let i = 0; i < vertCount; i++) {
    const x = pos[i * 3]
    const y = pos[i * 3 + 1]
    const z = pos[i * 3 + 2]

    if (x < minX) {
      minX = x
    }

    if (y < minY) {
      minY = y
    }

    if (z < minZ) {
      minZ = z
    }

    if (x > maxX) {
      maxX = x
    }

    if (y > maxY) {
      maxY = y
    }

    if (z > maxZ) {
      maxZ = z
    }
  }

  const vol = Math.max((maxX - minX) * (maxY - minY) * (maxZ - minZ), 1e-12)
  const cellSize = Math.cbrt(vol / MAX_PROXY_VERTS)

  interface Cluster {
    sx: number
    sy: number
    sz: number
    count: number
    weights: Map<number, number>
  }

  const clusters: Cluster[] = []
  const keyToCluster = new Map<string, number>()
  const clusterOf = new Int32Array(vertCount)

  for (let i = 0; i < vertCount; i++) {
    const cx = Math.floor((pos[i * 3] - minX) / cellSize)
    const cy = Math.floor((pos[i * 3 + 1] - minY) / cellSize)
    const cz = Math.floor((pos[i * 3 + 2] - minZ) / cellSize)
    const key = `${cx},${cy},${cz}`
    let cid = keyToCluster.get(key)

    if (cid === undefined) {
      cid = clusters.length
      keyToCluster.set(key, cid)
      clusters.push({ sx: 0, sy: 0, sz: 0, count: 0, weights: new Map() })
    }

    const cluster = clusters[cid]
    cluster.sx += pos[i * 3]
    cluster.sy += pos[i * 3 + 1]
    cluster.sz += pos[i * 3 + 2]
    cluster.count++
    clusterOf[i] = cid

    for (let j = 0; j < 4; j++) {
      const bone = si[i * siSize + j]
      const w = sw[i * swSize + j]

      if (w > 0) {
        cluster.weights.set(bone, (cluster.weights.get(bone) ?? 0) + w)
      }
    }
  }

  const proxyCount = clusters.length
  const positions = new Float32Array(proxyCount * 3)
  const skinIndex = new Uint16Array(proxyCount * 4)
  const skinWeight = new Float32Array(proxyCount * 4)

  for (let c = 0; c < proxyCount; c++) {
    const cluster = clusters[c]
    positions[c * 3] = cluster.sx / cluster.count
    positions[c * 3 + 1] = cluster.sy / cluster.count
    positions[c * 3 + 2] = cluster.sz / cluster.count

    const top = [...cluster.weights.entries()].sort((a, b) => b[1] - a[1]).slice(0, 4)
    let sum = 0

    for (const [, w] of top) {
      sum += w
    }

    for (let j = 0; j < 4; j++) {
      if (j < top.length && sum > 0) {
        skinIndex[c * 4 + j] = top[j][0]
        skinWeight[c * 4 + j] = top[j][1] / sum
      } else {
        skinIndex[c * 4 + j] = 0
        skinWeight[c * 4 + j] = 0
      }
    }
  }

  const tris: number[] = []

  for (let t = 0; t < idx.length; t += 3) {
    const ca = clusterOf[idx[t]]
    const cb = clusterOf[idx[t + 1]]
    const cc = clusterOf[idx[t + 2]]

    if (ca === cb || cb === cc || ca === cc) {
      continue
    }

    tris.push(ca, cb, cc)
  }

  return {
    positions,
    skinIndex,
    skinWeight,
    index: new Uint32Array(tris)
  }
}
