import * as THREE from 'three'

/** Client render style, mirroring the backend's seed-style routing
 * (`CompanionModel.style`). `anime` = NPR toon pipeline, `realistic` =
 * stock PBR. */
export type RenderStyle = 'anime' | 'realistic'

export interface ToonProfile {
  /** Linear luminance of each ramp band (0..1); band count = array length. */
  rampSteps: readonly number[]
  rimColor: THREE.Color
  rimPower: number
  rimStrength: number
  outlineColor: THREE.Color
  /** NDC units; ≈0.005 renders ~1.5px at 300×360 @DPR1.5. */
  outlineThickness: number
}

export type ToonProfileName = 'soft' | 'classic' | 'hard'

export const TOON_PROFILES: Record<ToonProfileName, ToonProfile> = {
  // Genshin-adjacent default: three bands + gentle rim + hairline outline.
  soft: {
    rampSteps: [0.55, 0.8, 1.0],
    rimColor: new THREE.Color(0.9, 0.92, 1.0),
    rimPower: 2.5,
    rimStrength: 0.5,
    outlineColor: new THREE.Color(0.08, 0.05, 0.09),
    outlineThickness: 0.005
  },
  classic: {
    rampSteps: [0.45, 0.85, 1.0],
    rimColor: new THREE.Color(0.85, 0.9, 1.0),
    rimPower: 3.0,
    rimStrength: 0.65,
    outlineColor: new THREE.Color(0.05, 0.04, 0.06),
    outlineThickness: 0.007
  },
  hard: {
    rampSteps: [0.4, 1.0],
    rimColor: new THREE.Color(1.0, 0.95, 0.9),
    rimPower: 3.5,
    rimStrength: 0.8,
    outlineColor: new THREE.Color(0.02, 0.02, 0.03),
    outlineThickness: 0.009
  }
}
