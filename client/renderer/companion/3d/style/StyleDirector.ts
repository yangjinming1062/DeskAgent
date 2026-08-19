import * as THREE from 'three'

import { log } from '@/shared/lib/log'

import { bakeFacialNormals, setFacialNormals } from './face-normals'
import { buildOutlineHull, createClassicOutlineMaterial, createNodeOutlineMaterial, OUTLINE_SUFFIX } from './outline'
import {
  applyProfileToUniforms,
  createClassicToonTwin,
  createSharedStyleUniforms,
  createToonTwin,
  isToonMaterial,
  type PbrSource,
  type SharedStyleUniforms
} from './toon-materials'
import { createToonRampTexture } from './toon-ramp'
import { type RenderStyle, TOON_PROFILES, type ToonProfile, type ToonProfileName } from './types'

export type TwinMaterial = ReturnType<typeof createToonTwin> | ReturnType<typeof createClassicToonTwin>

// Materials that own their vertex pipeline (the TSL cloth solver writes
// positionNode/normalNode) render as-is — toon twins would break the solver
// contract. One rule covers both physics backends: the CPU backend's plain
// materials carry no positionNode and stay toon-able.
const ownsVertexPipeline = (m: THREE.Material): boolean => (m as { positionNode?: unknown }).positionNode != null

const isHull = (obj: THREE.Object3D): boolean => obj.name.endsWith(OUTLINE_SUFFIX)

interface MeshStash {
  materials: THREE.Material[]
  isArray: boolean
}

const STASH_KEY = 'spiritPbr'

export interface StyleDirectorOptions {
  /** True on the WebGPU / WebGL2-node tiers (shared node-material code
   * path); false on the classic WebGLRenderer. */
  nodePipeline: boolean
  profile?: ToonProfileName
}

/**
 * Orchestrates the NPR (anime toon) render style over a character root:
 * toon twin materials, inverted-hull outlines, facial normal baking, and a
 * PBR ⇄ NPR hot switch that keeps the wardrobe texture pipeline working in
 * both modes. The director is renderer-agnostic — the tier only picks twin
 * and outline material implementations.
 */
export class StyleDirector {
  private readonly nodePipeline: boolean
  private readonly uniforms: SharedStyleUniforms
  private profile: ToonProfile
  private rampTexture: THREE.DataTexture
  private readonly outlineMaterial: THREE.Material
  private style: RenderStyle = 'realistic'
  private root: THREE.Object3D | null = null
  private readonly twinCache = new Map<PbrSource, TwinMaterial>()
  private readonly hulls: THREE.Mesh[] = []

  constructor(opts: StyleDirectorOptions) {
    this.nodePipeline = opts.nodePipeline
    this.profile = TOON_PROFILES[opts.profile ?? 'soft']
    this.uniforms = createSharedStyleUniforms(this.profile)
    this.rampTexture = createToonRampTexture(this.profile.rampSteps)
    this.outlineMaterial = this.nodePipeline
      ? createNodeOutlineMaterial(this.uniforms)
      : createClassicOutlineMaterial(this.uniforms)
  }

  get currentStyle(): RenderStyle {
    return this.style
  }

  /**
   * Bind a freshly loaded character root. Bakes facial normals at rest pose
   * and mounts outline hulls before MorphController discovery so hull clones
   * pick up morph/blink driving. If the director is already in anime mode
   * (e.g. a model reload), the new root is immediately toon-ified.
   */
  attachCharacter(root: THREE.Object3D, headBone: THREE.Bone | null, neckBone: THREE.Bone | null): void {
    this.root = root
    bakeFacialNormals(root, headBone, neckBone)
    this.buildHulls(root)

    if (this.style === 'anime') {
      this.applyAnime(root)
    }
  }

  /** Mount hulls (and twins, when in anime mode) for an assembled wardrobe
   * unit group. */
  attachUnit(group: THREE.Object3D): void {
    this.buildHulls(group)

    if (this.style === 'anime') {
      this.applyAnime(group)
    }
  }

  /**
   * Hot-switch the render style. `onRestorePbr` runs after switching back
   * to realistic so the caller can replay wardrobe channel textures onto
   * the restored PBR materials (anime-mode binds only reached the twins).
   */
  setStyle(style: RenderStyle, onRestorePbr?: () => void): void {
    if (style === this.style) {
      return
    }

    const previous = this.style
    this.style = style

    try {
      if (style === 'anime') {
        if (this.root) {
          this.applyAnime(this.root)
        }
      } else {
        this.applyRealistic()
        onRestorePbr?.()
      }
    } catch (err) {
      // A failed swap must never take down the render loop — roll back to
      // the previous (working) style and surface the reason.
      log.warn('style-director', 'style switch failed, rolling back:', err)
      this.style = previous

      try {
        if (previous === 'anime' && this.root) {
          this.applyAnime(this.root)
        } else {
          this.applyRealistic()
        }
      } catch {
        // Give up quietly — the next switch retries from a known state.
      }
    }
  }

  /** Swap the toon look without rebuilding the pipeline. */
  setProfile(name: ToonProfileName): void {
    this.profile = TOON_PROFILES[name]
    applyProfileToUniforms(this.uniforms, this.profile)

    const next = createToonRampTexture(this.profile.rampSteps)

    for (const twin of this.twinCache.values()) {
      twin.gradientMap = next
    }

    this.rampTexture.dispose()
    this.rampTexture = next

    if (!this.nodePipeline) {
      ;(this.outlineMaterial as THREE.MeshBasicMaterial).color.copy(this.profile.outlineColor)
    }
  }

  /** Per-load teardown: restore PBR on the old root and drop hulls/twins.
   * The shared uniforms, outline material, and ramp texture survive for the
   * next attach. */
  reset(): void {
    if (this.style === 'anime') {
      try {
        this.applyRealistic()
        this.style = 'anime'
      } catch {
        // Old root is being torn down anyway.
      }
    }

    for (const hull of this.hulls) {
      hull.parent?.remove(hull)
    }

    this.hulls.length = 0

    for (const twin of this.twinCache.values()) {
      twin.dispose()
    }

    this.twinCache.clear()
    this.root = null
  }

  dispose(): void {
    this.reset()
    this.outlineMaterial.dispose()
    this.rampTexture.dispose()
  }

  // ── internals ─────────────────────────────────────────────────────

  private buildHulls(container: THREE.Object3D): void {
    container.traverse(child => {
      if (!(child instanceof THREE.Mesh) || isHull(child)) {
        return
      }

      if (child.children.some(isHull)) {
        return
      }

      const first = Array.isArray(child.material) ? child.material[0] : child.material

      if (!first || isToonMaterial(first) || ownsVertexPipeline(first)) {
        return
      }

      this.hulls.push(buildOutlineHull(child, this.outlineMaterial))
    })
  }

  private twinFor(src: PbrSource): TwinMaterial {
    const cached = this.twinCache.get(src)

    if (cached) {
      return cached
    }

    const twin = this.nodePipeline
      ? createToonTwin(src, this.rampTexture, this.uniforms)
      : createClassicToonTwin(src, this.rampTexture, this.uniforms)

    this.twinCache.set(src, twin)

    return twin
  }

  private applyAnime(container: THREE.Object3D): void {
    container.traverse(child => {
      if (!(child instanceof THREE.Mesh) || isHull(child)) {
        return
      }

      const materials = Array.isArray(child.material) ? child.material : [child.material]
      let changed = false

      const replaced = materials.map(m => {
        if (!this.isToonablePbr(m)) {
          return m
        }

        changed = true

        return this.twinFor(m)
      })

      if (!changed) {
        return
      }

      if (!child.userData[STASH_KEY]) {
        child.userData[STASH_KEY] = {
          materials,
          isArray: Array.isArray(child.material)
        } satisfies MeshStash
      }

      child.material = (child.userData[STASH_KEY] as MeshStash).isArray ? replaced : replaced[0]
    })

    setFacialNormals(container, 'toon')

    for (const hull of this.hulls) {
      hull.visible = true
    }
  }

  private applyRealistic(): void {
    const container = this.root

    if (!container) {
      return
    }

    container.traverse(child => {
      if (!(child instanceof THREE.Mesh) || isHull(child)) {
        return
      }

      const stash = child.userData[STASH_KEY] as MeshStash | undefined

      if (!stash) {
        return
      }

      child.material = stash.isArray ? stash.materials : stash.materials[0]
      delete child.userData[STASH_KEY]
    })

    setFacialNormals(container, 'original')

    for (const hull of this.hulls) {
      hull.visible = false
    }
  }

  private isToonablePbr(m: THREE.Material | null): m is PbrSource {
    if (!m) {
      return false
    }

    if (ownsVertexPipeline(m) || isToonMaterial(m)) {
      return false
    }

    return (
      m instanceof THREE.MeshStandardMaterial ||
      (m as { isMeshStandardNodeMaterial?: boolean }).isMeshStandardNodeMaterial === true
    )
  }
}
