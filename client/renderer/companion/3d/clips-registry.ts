import type * as THREE from 'three'

import { AQUATIC_CLIPS } from './clips-aquatic'
import { AVIAN_CLIPS } from './clips-avian'
import { BIPED_CLIPS, buildClip, type ClipDef } from './clips-biped'
import { HEXAPOD_CLIPS } from './clips-hexapod'
import { OCTOPOD_CLIPS } from './clips-octopod'
import { QUADRUPED_CLIPS } from './clips-quadruped'
import { SERPENTINE_CLIPS } from './clips-serpentine'

export type RigType = 'biped' | 'quadruped' | 'avian' | 'serpentine' | 'aquatic' | 'hexapod' | 'octopod'

export const SUPPORTED_RIG_TYPES: ReadonlyArray<RigType> = [
  'biped',
  'quadruped',
  'avian',
  'serpentine',
  'aquatic',
  'hexapod',
  'octopod'
]

export function getClipDefs(rigType: RigType | string | null | undefined): Readonly<Record<string, ClipDef>> {
  switch (rigType) {
    case 'biped':
      return BIPED_CLIPS

    case 'quadruped':
      return QUADRUPED_CLIPS

    case 'avian':
      return AVIAN_CLIPS

    case 'serpentine':
      return SERPENTINE_CLIPS

    case 'aquatic':
      return AQUATIC_CLIPS

    case 'hexapod':
      return HEXAPOD_CLIPS

    case 'octopod':
      return OCTOPOD_CLIPS

    default:
      return BIPED_CLIPS
  }
}

export function getClipNames(rigType: RigType | string | null | undefined): readonly string[] {
  return Object.keys(getClipDefs(rigType))
}

export function buildClipsForRig(
  rigType: RigType | string | null | undefined,
  restQuats?: ReadonlyMap<string, THREE.Quaternion>
): THREE.AnimationClip[] {
  const defs = getClipDefs(rigType)

  return Object.values(defs).map(def => buildClip(def, restQuats))
}

interface RigClipSummary {
  rig_type: RigType
  count: number
  names: readonly string[]
}

export function summarizeRigLibraries(): readonly RigClipSummary[] {
  return SUPPORTED_RIG_TYPES.map(rig => {
    const defs = getClipDefs(rig)

    return {
      rig_type: rig,
      count: Object.keys(defs).length,
      names: Object.keys(defs)
    }
  })
}
