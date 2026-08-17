import * as THREE from 'three'
import type { float } from 'three/tsl'
import {
  BRDF_Lambert,
  diffuseColor,
  materialReference,
  normalView,
  oneMinus,
  positionViewDirection,
  pow,
  texture,
  uniform,
  vec2,
  vec3
} from 'three/tsl'
import { LightingModel, MeshToonNodeMaterial } from 'three/webgpu'

import { createToonRampTexture } from './toon-ramp'
import type { ToonProfile } from './types'

// Vendored from three's ToonLightingModel (src/nodes/functions/
// ToonLightingModel.js) with one fix: the upstream `direct()` lights with
// `normalGeometry` — the raw, un-skinned object-space normal attribute —
// while `lightDirection` is view-space. On skinned meshes that space/skin
// mismatch smears the cel bands across the wrong faces. `normalView` is the
// skinned + normalMap-transformed view-space normal, matching what the
// physical lighting model uses.
//
// 'three/tsl' does not export the Node/NodeBuilder instance types, so the
// overrides borrow the base signatures via Parameters<...> and narrow the
// builder context structurally.
interface ToonIndirectContext {
  context: {
    ambientOcclusion: { mulAssign(node: unknown): void }
    irradiance: { mul(node: unknown): unknown }
    reflectedLight: { indirectDiffuse: { addAssign(node: unknown): void; mulAssign(node: unknown): void } }
  }
}

// Parameterized TSL node types aren't exported from 'three/tsl' — alias them
// from constructors and narrow the LightingModel inputs structurally at the
// boundary (they are vec3 nodes at runtime by contract).
type Vec3Node = ReturnType<typeof vec3>
type FloatNode = ReturnType<typeof float>

let _fallbackRamp: THREE.DataTexture | null = null

class SkinnedToonLightingModel extends LightingModel {
  private readonly ramp: THREE.Texture

  constructor(ramp: THREE.Texture | null) {
    super()
    // The node twin always assigns gradientMap before its first setup; the
    // fallback keeps a bare `new ToonNodeMaterial()` compilable anyway.
    _fallbackRamp ??= createToonRampTexture([0.55, 0.8, 1.0])
    this.ramp = ramp ?? _fallbackRamp
  }

  override direct(...[{ lightDirection, lightColor, reflectedLight }]: Parameters<LightingModel['direct']>): void {
    const dotNL = normalView.dot(lightDirection as unknown as Vec3Node)
    const coord = vec2(dotNL.mul(0.5).add(0.5) as unknown as FloatNode, 0)
    const gradient = texture(this.ramp, coord)

    const irradiance = vec3(gradient.r).mul(lightColor as unknown as Vec3Node) as unknown as Vec3Node

    ;(reflectedLight.directDiffuse as unknown as { addAssign(node: unknown): void }).addAssign(
      irradiance.mul(BRDF_Lambert({ diffuseColor: diffuseColor.rgb }) as unknown as Vec3Node)
    )
  }

  override indirect(...[builder]: Parameters<LightingModel['indirect']>): void {
    const { ambientOcclusion, irradiance, reflectedLight } = (builder as unknown as ToonIndirectContext).context

    reflectedLight.indirectDiffuse.addAssign(irradiance.mul(BRDF_Lambert({ diffuseColor })))
    reflectedLight.indirectDiffuse.mulAssign(ambientOcclusion)
  }
}

export class ToonNodeMaterial extends MeshToonNodeMaterial {
  // Declared public to match the @types signature (the runtime treats it as
  // an internal hook; nothing outside the style layer calls it).
  override setupLightingModel(): ReturnType<MeshToonNodeMaterial['setupLightingModel']> {
    return new SkinnedToonLightingModel(this.gradientMap) as unknown as ReturnType<
      MeshToonNodeMaterial['setupLightingModel']
    >
  }
}

/** One JS value shared by both renderer tiers: node pipelines read
 * `node.value`, the classic onBeforeCompile path reads `classic.value` from
 * shader.uniforms. Keeps profile tweaks live across a hot switch. Fields
 * stay explicit so each `uniform()` picks its properly-typed overload. */
function buildSharedStyleUniforms(profile: ToonProfile) {
  return {
    rimColor: { node: uniform(profile.rimColor.clone()), classic: { value: profile.rimColor.clone() } },
    rimPower: { node: uniform(profile.rimPower), classic: { value: profile.rimPower } },
    rimStrength: { node: uniform(profile.rimStrength), classic: { value: profile.rimStrength } },
    outlineColor: { node: uniform(profile.outlineColor.clone()), classic: { value: profile.outlineColor.clone() } },
    outlineThickness: { node: uniform(profile.outlineThickness), classic: { value: profile.outlineThickness } }
  }
}

export type SharedStyleUniforms = ReturnType<typeof buildSharedStyleUniforms>

export function createSharedStyleUniforms(profile: ToonProfile): SharedStyleUniforms {
  return buildSharedStyleUniforms(profile)
}

export function applyProfileToUniforms(u: SharedStyleUniforms, profile: ToonProfile): void {
  u.rimColor.node.value.copy(profile.rimColor)
  u.rimColor.classic.value.copy(profile.rimColor)
  u.rimPower.node.value = profile.rimPower
  u.rimPower.classic.value = profile.rimPower
  u.rimStrength.node.value = profile.rimStrength
  u.rimStrength.classic.value = profile.rimStrength
  u.outlineColor.node.value.copy(profile.outlineColor)
  u.outlineColor.classic.value.copy(profile.outlineColor)
  u.outlineThickness.node.value = profile.outlineThickness
  u.outlineThickness.classic.value = profile.outlineThickness
}

/** Rim term shared by both tiers: pow(1 - N·V, power) * color * strength. */
function makeRimNode(u: SharedStyleUniforms) {
  const fresnel = pow(oneMinus(normalView.dot(positionViewDirection).saturate()), u.rimPower.node)

  return fresnel.mul(u.rimColor.node).mul(u.rimStrength.node)
}

// NodeMaterial reads `emissiveNode` in setupLighting (added after lighting,
// before tonemap) — the natural rim hook without subclassing further. The
// @types declaration lags the runtime, hence the structural holder.
interface EmissiveNodeHolder {
  emissiveNode: unknown
}

export function isToonMaterial(m: THREE.Material): m is ToonNodeMaterial | THREE.MeshToonMaterial {
  return (
    (m as { isMeshToonNodeMaterial?: boolean }).isMeshToonNodeMaterial === true || m instanceof THREE.MeshToonMaterial
  )
}

/** A toon-able source material: GLB PBR materials come in both flavors
 * (MeshStandardMaterial / MeshStandardNodeMaterial) depending on the
 * renderer tier — structurally typed by the slots the twin copies.
 * Cloth-solver materials are excluded upstream (positionNode owned by the
 * physics backend). */
export interface PbrSource extends THREE.Material {
  color: THREE.Color
  map: THREE.Texture | null
  normalMap: THREE.Texture | null
  normalScale: THREE.Vector2
  emissive: THREE.Color
  emissiveMap: THREE.Texture | null
}

function copyColorableSlots(src: PbrSource, twin: THREE.MeshToonMaterial | ToonNodeMaterial): void {
  twin.color.copy(src.color)
  twin.map = src.map
  twin.normalMap = src.normalMap
  twin.normalScale.copy(src.normalScale)
  twin.emissive.copy(src.emissive)
  twin.emissiveMap = src.emissiveMap
  twin.transparent = src.transparent
  twin.opacity = src.opacity
  twin.alphaTest = src.alphaTest
  twin.side = src.side
  // userData carries the GLB base-texture markers (baseMap / baseNormalMap /
  // …) the wardrobe clear-fallback reads — twins must see the same refs.
  twin.userData = src.userData
}

/** Node-tier twin for WebGPU / WebGL2-node backends. */
export function createToonTwin(
  src: PbrSource,
  rampTexture: THREE.DataTexture,
  u: SharedStyleUniforms
): ToonNodeMaterial {
  const twin = new ToonNodeMaterial()
  copyColorableSlots(src, twin)
  twin.gradientMap = rampTexture
  // The material's own emissive rides along so GLB glow parts survive.
  ;(twin as unknown as EmissiveNodeHolder).emissiveNode = makeRimNode(u).add(
    materialReference('emissive', 'color') as unknown as Vec3Node
  )

  return twin
}

/** Classic-tier twin: stock MeshToonMaterial + onBeforeCompile rim. */
export function createClassicToonTwin(
  src: PbrSource,
  rampTexture: THREE.DataTexture,
  u: SharedStyleUniforms
): THREE.MeshToonMaterial {
  const twin = new THREE.MeshToonMaterial()
  copyColorableSlots(src, twin)
  twin.gradientMap = rampTexture

  twin.onBeforeCompile = shader => {
    shader.uniforms.uRimColor = u.rimColor.classic
    shader.uniforms.uRimPower = u.rimPower.classic
    shader.uniforms.uRimStrength = u.rimStrength.classic
    shader.fragmentShader = shader.fragmentShader
      .replace(
        'void main() {',
        ['uniform vec3 uRimColor;', 'uniform float uRimPower;', 'uniform float uRimStrength;', 'void main() {'].join(
          '\n'
        )
      )
      .replace(
        'vec3 outgoingLight = reflectedLight.directDiffuse + reflectedLight.indirectDiffuse + totalEmissiveRadiance;',
        [
          'float rimFresnel = pow( clamp( 1.0 - dot( normalize( vNormal ), normalize( vViewPosition ) ), 0.0, 1.0 ), uRimPower );',
          'vec3 rimLight = rimFresnel * uRimColor * uRimStrength;',
          'vec3 outgoingLight = reflectedLight.directDiffuse + reflectedLight.indirectDiffuse + totalEmissiveRadiance + rimLight;'
        ].join('\n')
      )
  }

  twin.customProgramCacheKey = (): string => 'spirit-toon-rim'

  return twin
}
