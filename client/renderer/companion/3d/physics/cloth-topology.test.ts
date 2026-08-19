import * as THREE from 'three'
import { describe, expect, it } from 'vitest'

import { buildAnchors, buildConstraints, buildVertexTriAdjacency } from './cloth-topology'

function planePositions(w = 0.2, h = 0.4, seg = 2): { base: Float32Array; count: number } {
  const geo = new THREE.PlaneGeometry(w, h, seg, seg)
  const base = new Float32Array(geo.attributes.position.array as ArrayLike<number>)

  return { base, count: geo.attributes.position.count }
}

describe('buildAnchors', () => {
  it('在 pinAll 模式下钉住每个顶点', () => {
    const { base, count } = planePositions()

    expect(Array.from(buildAnchors(base, count, true))).toEqual(new Array(count).fill(1))
  })

  it('只在高度范围的顶部带状区域设锚点', () => {
    const { base, count } = planePositions(0.2, 0.4, 2)
    const anchors = buildAnchors(base, count, false, 0.3)

    let minY = Infinity
    let maxY = -Infinity

    for (let i = 1; i < base.length; i += 3) {
      minY = Math.min(minY, base[i])
      maxY = Math.max(maxY, base[i])
    }

    const threshold = minY + (maxY - minY) * 0.3

    for (let i = 0; i < count; i++) {
      expect(anchors[i]).toBe(base[i * 3 + 1] >= threshold ? 1 : 0)
    }

    expect(anchors.some(a => a === 1)).toBe(true)
    expect(anchors.some(a => a === 0)).toBe(true)
  })
})

describe('buildConstraints', () => {
  it('从 index buffer 导出唯一的边与静态长度', () => {
    const geo = new THREE.PlaneGeometry(0.2, 0.2, 2, 2)
    const base = new Float32Array(geo.attributes.position.array as ArrayLike<number>)
    const idx = geo.index!.array
    const constraints = buildConstraints(idx, geo.attributes.position.count, base)!

    expect(constraints).not.toBeNull()
    expect(constraints.edges.length % 2).toBe(0)
    expect(constraints.rest.length).toBe(constraints.edges.length / 2)

    // 静态长度与原始位置完全一致。
    for (let e = 0; e < constraints.edges.length; e += 2) {
      const a = constraints.edges[e] * 3
      const b = constraints.edges[e + 1] * 3

      expect(constraints.rest[e / 2]).toBeCloseTo(
        Math.hypot(base[a] - base[b], base[a + 1] - base[b + 1], base[a + 2] - base[b + 2]),
        6
      )
    }
  })

  it('超出顶点上限时拒绝', () => {
    const { base } = planePositions()
    const idx = new THREE.PlaneGeometry(0.2, 0.2, 2, 2).index!.array

    expect(buildConstraints(idx, 100_000, base)).toBeNull()
  })
})

describe('buildVertexTriAdjacency', () => {
  it('每个三角形引用在角顶点上恰好被分配一次', () => {
    const geo = new THREE.PlaneGeometry(0.2, 0.2, 3, 3)
    const vertCount = geo.attributes.position.count
    const idx = geo.index!.array
    const { offsets, list } = buildVertexTriAdjacency(idx, vertCount)
    const triCount = Math.floor(idx.length / 3)

    expect(offsets.length).toBe(vertCount + 1)
    expect(offsets[0]).toBe(0)
    expect(offsets[vertCount]).toBe(idx.length)

    const refsPerVertex = new Map<number, number>()

    for (let t = 0; t < triCount; t++) {
      for (let k = 0; k < 3; k++) {
        const v = idx[t * 3 + k]
        refsPerVertex.set(v, (refsPerVertex.get(v) ?? 0) + 1)
      }
    }

    for (let v = 0; v < vertCount; v++) {
      const refs = offsets[v + 1] - offsets[v]

      expect(refs).toBe(refsPerVertex.get(v) ?? 0)

      const seen = new Set<number>()

      for (let r = offsets[v]; r < offsets[v + 1]; r++) {
        const t = list[r]

        expect(t).toBeLessThan(triCount)
        expect(seen.has(t)).toBe(false)
        seen.add(t)
      }
    }
  })
})
