import type { ClipDef } from './clips-biped'
import { buildStateClipsForBones } from './clips-biped'

export interface AvianBoneSlots {
  root: string
  spine: readonly string[]
  head: string
  jaw: string
  leftWing: readonly string[]
  rightWing: readonly string[]
  leftLeg: readonly string[]
  rightLeg: readonly string[]
  tail: readonly string[]
}

export const TRIPO_AVIAN_BONES: AvianBoneSlots = {
  root: 'Hips',
  spine: ['Spine', 'Spine1', 'Neck'],
  head: 'Head',
  jaw: 'Jaw',
  leftWing: ['LeftWing1', 'LeftWing2', 'LeftWing3'],
  rightWing: ['RightWing1', 'RightWing2', 'RightWing3'],
  leftLeg: ['LeftLeg', 'LeftFoot'],
  rightLeg: ['RightLeg', 'RightFoot'],
  tail: ['Tail1', 'Tail2', 'Tail3']
}

function _aManifest(name: string, duration: number, loop: boolean, category: ClipDef['category']): ClipDef {
  return {
    name,
    duration,
    loop,
    category,
    tracks: {
      [TRIPO_AVIAN_BONES.spine[0]]: [
        { t: 0, r: [0.04, 0.0, 0.03] as const },
        { t: duration / 2, r: [0.04, 0.0, 0.03] as const },
        { t: duration, r: [0.04, 0.0, 0.03] as const }
      ]
    }
  }
}

export const AVIAN_CLIPS: Readonly<Record<string, ClipDef>> = {
  ...buildStateClipsForBones(TRIPO_AVIAN_BONES.spine[0], TRIPO_AVIAN_BONES.head),
  avian_idle: _aManifest('avian_idle', 4, true, 'state'),
  avian_perch: _aManifest('avian_perch', 5, true, 'state'),
  avian_sleep: _aManifest('avian_sleep', 6, true, 'state'),
  avian_listen: _aManifest('avian_listen', 3, true, 'state'),
  avian_alert: _aManifest('avian_alert', 1.2, false, 'state'),
  avian_walk: _aManifest('avian_walk', 1, true, 'locomotion'),
  avian_hop: _aManifest('avian_hop', 0.6, true, 'locomotion'),
  avian_fly_glide: _aManifest('avian_fly_glide', 2.5, true, 'locomotion'),
  avian_fly_flap: _aManifest('avian_fly_flap', 0.8, true, 'locomotion'),
  avian_takeoff: _aManifest('avian_takeoff', 1.2, false, 'locomotion'),
  avian_land: _aManifest('avian_land', 1, false, 'locomotion'),
  avian_preen: _aManifest('avian_preen', 3, true, 'interaction'),
  avian_sing: _aManifest('avian_sing', 4, true, 'interaction'),
  avian_peck: _aManifest('avian_peck', 0.8, false, 'interaction'),
  avian_happy_chirp: _aManifest('avian_happy_chirp', 2, true, 'emotion-positive'),
  avian_scared_flap: _aManifest('avian_scared_flap', 1.5, true, 'emotion-negative'),
  avian_mating_display: _aManifest('avian_mating_display', 3, true, 'ritual'),
  avian_build_nest: _aManifest('avian_build_nest', 4, true, 'daily')
}
