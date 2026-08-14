import * as THREE from 'three'

import { log } from '@/shared/lib/log'

import type { BodyCollider } from './BodyCollider'
import {
  ANCHOR_RATIO,
  buildAnchors,
  buildConstraints,
  COLLIDER_RADII,
  DAMPING,
  GRAVITY,
  ITERATIONS,
  SKIN_CLEARANCE
} from './physics/cloth-topology'
import { cpuSkinPoint } from './skinning'
import { boneSuffix } from './types'

/** Lightweight Verlet cloth simulation for physics=cloth garment units. */

const _v = new THREE.Vector3()
const _inv = new THREE.Matrix4()

export class ClothSolver {
  private readonly mesh: THREE.Mesh
  private readonly skeleton: THREE.Skeleton
  private readonly bindMatrix: THREE.Matrix4 | null
  private readonly pinAll: boolean
  private readonly bodyCollider: BodyCollider | null
  private readonly clearance: number
  private readonly gravity: number
  private readonly damping: number
  private readonly iterations: number

  private readonly base: Float32Array
  private readonly prev: Float32Array
  private readonly pos: Float32Array
  private readonly anchor: Uint8Array
  private readonly edges: Uint32Array | null
  private readonly rest: Float32Array | null
  private readonly skinIndex: Uint16Array | Uint8Array | null
  private readonly skinWeight: Float32Array | null

  private readonly colliders: { bone: THREE.Bone; radius: number }[] = []
  private readonly colliderPos: THREE.Vector3[] = []
  private initialized = false

  constructor(
    mesh: THREE.Mesh,
    skeleton: THREE.Skeleton,
    bindMatrix: THREE.Matrix4 | null = null,
    opts: { pinAll?: boolean; bodyCollider?: BodyCollider | null; clearance?: number } = {}
  ) {
    this.mesh = mesh
    this.skeleton = skeleton
    this.bindMatrix = bindMatrix
    this.pinAll = opts.pinAll ?? false
    this.bodyCollider = opts.bodyCollider ?? null
    this.clearance = opts.clearance ?? SKIN_CLEARANCE
    this.gravity = this.pinAll ? 0 : GRAVITY
    this.damping = DAMPING
    this.iterations = this.pinAll ? 0 : ITERATIONS

    const geo = mesh.geometry
    const posAttr = geo.getAttribute('position')
    const count = posAttr?.count ?? 0

    if (!posAttr || count === 0) {
      this.base = new Float32Array(0)
      this.pos = new Float32Array(0)
      this.prev = new Float32Array(0)
      this.anchor = new Uint8Array(0)
      this.edges = null
      this.rest = null
      this.skinIndex = null
      this.skinWeight = null

      return
    }

    this.base = new Float32Array(posAttr.array as ArrayLike<number>)
    this.pos = new Float32Array(this.base)
    this.prev = new Float32Array(this.base)
    this.anchor = buildAnchors(this.base, count, this.pinAll, ANCHOR_RATIO)

    const si = geo.getAttribute('skinIndex')
    const sw = geo.getAttribute('skinWeight')

    this.skinIndex = si ? (si.array as Uint16Array | Uint8Array) : null
    this.skinWeight = sw ? (sw.array as Float32Array) : null

    // Structural constraints from the index buffer (unique edges). Skin mode
    // has no free vertices, so it skips edge constraints entirely.
    const constraints = !this.pinAll && geo.index ? buildConstraints(geo.index.array, count, this.base) : null

    this.edges = constraints?.edges ?? null
    this.rest = constraints?.rest ?? null

    if (!this.pinAll && !constraints) {
      log.warn('cloth', 'cloth mesh has no index buffer or exceeds vertex budget — constraints disabled')
    }

    // Bone sphere colliders are only built when bodyCollider is absent.
    // When bodyCollider is available, accurate body surface mesh collision is used
    // and coarse bone spheres are disabled so they do not push beyond the surface.
    if (!this.pinAll && !this.bodyCollider) {
      for (const bone of skeleton.bones) {
        const radius = COLLIDER_RADII[boneSuffix(bone.name)]

        if (radius !== undefined) {
          this.colliders.push({ bone, radius })
          this.colliderPos.push(new THREE.Vector3())
        }
      }
    }
  }

  private frameTick = 0

  /** Step simulation and update vertex positions. */
  update(dtRaw: number): void {
    if (this.anchor.length === 0) {
      return
    }

    const dt = Math.min(Math.max(dtRaw, 1 / 120), 1 / 30)
    const geo = this.mesh.geometry

    this.mesh.updateWorldMatrix(true, false)
    // Single mesh-to-skeleton-space inverse shared by the two methods below.
    _inv.copy(this.mesh.matrixWorld).invert()

    if (!this.initialized) {
      // First frame: settle everything onto the skinned pose so the cloth
      // doesn't visibly snap from the bind pose.
      this.writeSkinnedTargets(true)
      this.prev.set(this.pos)
      this.initialized = true
    } else {
      this.writeSkinnedTargets(false)
    }

    this.refreshColliders()
    this.resolveBodyCollisions()

    const g = this.gravity * dt * dt
    const pos = this.pos
    const prev = this.prev
    const count = this.anchor.length

    // Integrate free vertices (anchors were pinned by writeSkinnedTargets).
    for (let i = 0; i < count; i++) {
      if (this.anchor[i]) {
        continue
      }

      const o = i * 3

      for (let c = 0; c < 3; c++) {
        const cur = pos[o + c]
        const vel = (cur - prev[o + c]) * this.damping

        prev[o + c] = cur
        pos[o + c] = cur + vel + (c === 1 ? g : 0)
      }
    }

    for (let it = 0; it < this.iterations; it++) {
      this.satisfyEdges()
      this.resolveCollisions()
    }

    const attr = geo.attributes.position

    ;(attr.array as Float32Array).set(pos)
    attr.needsUpdate = true

    // Halve normal recompute — 60 fps hems look identical to 30 fps and this
    // is the largest single cost on the hot path.
    if (++this.frameTick % 2 === 0) {
      geo.computeVertexNormals()
    }
  }

  /** Compute CPU-skinned target positions for anchored vertices. */
  private writeSkinnedTargets(pinAll: boolean): void {
    if (!this.skinIndex || !this.skinWeight) {
      return
    }

    if (
      !this.skeleton.boneMatrices ||
      (this.skeleton.boneMatrices[0] === 0 &&
        this.skeleton.boneMatrices[5] === 0 &&
        this.skeleton.boneMatrices[10] === 0)
    ) {
      this.skeleton.update()
    }

    const bm = this.skeleton.boneMatrices as unknown as Float32Array

    if (!bm) {
      return
    }

    const pos = this.pos
    const prev = this.prev
    const si = this.skinIndex
    const sw = this.skinWeight
    const count = this.anchor.length

    // mesh.matrixWorld inverse precomputed by update() and shared with refreshColliders.

    for (let i = 0; i < count; i++) {
      if (!pinAll && !this.anchor[i]) {
        continue
      }

      const o = i * 3

      cpuSkinPoint(this.base[o], this.base[o + 1], this.base[o + 2], si, sw, i * 4, bm, this.bindMatrix, _v)
      _v.applyMatrix4(_inv)
      pos[o] = _v.x
      pos[o + 1] = _v.y
      pos[o + 2] = _v.z
      prev[o] = pos[o]
      prev[o + 1] = pos[o + 1]
      prev[o + 2] = pos[o + 2]
    }
  }

  private satisfyEdges(): void {
    if (!this.edges || !this.rest) {
      return
    }

    const pos = this.pos
    const e = this.edges

    for (let i = 0; i < e.length; i += 2) {
      const a = e[i] * 3
      const b = e[i + 1] * 3
      const dx = pos[b] - pos[a]
      const dy = pos[b + 1] - pos[a + 1]
      const dz = pos[b + 2] - pos[a + 2]
      const d2 = dx * dx + dy * dy + dz * dz
      const r = this.rest[i / 2]

      if (d2 < 1e-12) {
        continue
      }

      const d = Math.sqrt(d2)

      if (d === r) {
        continue
      }

      const diff = (d - r) / d
      const aFixed = this.anchor[e[i]] === 1
      const bFixed = this.anchor[e[i + 1]] === 1
      // Split correction; a fixed endpoint absorbs the whole correction.
      const wa = aFixed ? 0 : bFixed ? 1 : 0.5
      const wb = bFixed ? 0 : aFixed ? 1 : 0.5

      pos[a] += dx * diff * wa
      pos[a + 1] += dy * diff * wa
      pos[a + 2] += dz * diff * wa
      pos[b] -= dx * diff * wb
      pos[b + 1] -= dy * diff * wb
      pos[b + 2] -= dz * diff * wb
    }
  }

  private refreshColliders(): void {
    // bone.matrixWorld is maintained by the renderer each frame; reading it
    // directly skips the per-collider ancestor walk. One-frame lag contract
    // already documented. mesh.matrixWorld inverse precomputed by update().

    for (let i = 0; i < this.colliders.length; i++) {
      const bone = this.colliders[i].bone
      this.colliderPos[i].setFromMatrixPosition(bone.matrixWorld).applyMatrix4(_inv)
    }
  }

  /** Push pinned skin vertices out of the animated body surface. */
  private resolveBodyCollisions(): void {
    if (!this.bodyCollider) {
      return
    }

    const pos = this.pos
    const world = this.mesh.matrixWorld

    for (let i = 0; i < this.anchor.length; i++) {
      if (!this.anchor[i]) {
        continue
      }

      const o = i * 3

      _v.set(pos[o], pos[o + 1], pos[o + 2]).applyMatrix4(world)

      if (this.bodyCollider.resolve(_v, this.clearance)) {
        _v.applyMatrix4(_inv)
        pos[o] = _v.x
        pos[o + 1] = _v.y
        pos[o + 2] = _v.z
      }
    }
  }

  private resolveCollisions(): void {
    const pos = this.pos
    const world = this.mesh.matrixWorld

    for (let i = 0; i < this.anchor.length; i++) {
      if (this.anchor[i]) {
        continue
      }

      const o = i * 3

      if (this.bodyCollider) {
        _v.set(pos[o], pos[o + 1], pos[o + 2]).applyMatrix4(world)

        if (this.bodyCollider.resolve(_v, this.clearance)) {
          _v.applyMatrix4(_inv)
          pos[o] = _v.x
          pos[o + 1] = _v.y
          pos[o + 2] = _v.z
        }
      } else {
        for (let c = 0; c < this.colliderPos.length; c++) {
          const p = this.colliderPos[c]
          const r = this.colliders[c].radius
          const dx = pos[o] - p.x
          const dy = pos[o + 1] - p.y
          const dz = pos[o + 2] - p.z
          const d2 = dx * dx + dy * dy + dz * dz

          if (d2 >= r * r || d2 < 1e-9) {
            continue
          }

          const d = Math.sqrt(d2)
          const push = (r - d) / d

          pos[o] += dx * push
          pos[o + 1] += dy * push
          pos[o + 2] += dz * push
        }
      }
    }
  }
}
