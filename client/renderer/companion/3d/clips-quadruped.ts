import type { ClipDef } from './clips-biped'
import { buildStateClipsForBones } from './clips-biped'

export interface QuadrupedBoneSlots {
  root: string
  spine: readonly string[]
  head: string
  jaw: string
  leftFrontLeg: readonly string[]
  rightFrontLeg: readonly string[]
  leftHindLeg: readonly string[]
  rightHindLeg: readonly string[]
  tail: readonly string[]
}

export const TRIPO_QUADRUPED_BONES: QuadrupedBoneSlots = {
  root: 'Hips',
  spine: ['Spine', 'Spine1', 'Spine2', 'Neck'],
  head: 'Head',
  jaw: 'Jaw',
  leftFrontLeg: ['LeftFrontLeg', 'LeftFrontKnee', 'LeftFrontFoot'],
  rightFrontLeg: ['RightFrontLeg', 'RightFrontKnee', 'RightFrontFoot'],
  leftHindLeg: ['LeftHindLeg', 'LeftHindKnee', 'LeftHindFoot'],
  rightHindLeg: ['RightHindLeg', 'RightHindKnee', 'RightHindFoot'],
  tail: ['Tail', 'Tail1', 'Tail2']
}

function _qManifest(name: string, duration: number, loop: boolean, category: ClipDef['category']): ClipDef {
  return {
    name,
    duration,
    loop,
    category,
    tracks: {
      [TRIPO_QUADRUPED_BONES.spine[0]]: [
        { t: 0, r: [0.02, 0.0, 0.04] as const },
        { t: duration / 2, r: [0.02, 0.0, 0.04] as const },
        { t: duration, r: [0.02, 0.0, 0.04] as const }
      ]
    }
  }
}

export const QUADRUPED_CLIPS: Readonly<Record<string, ClipDef>> = {
  ...buildStateClipsForBones(TRIPO_QUADRUPED_BONES.spine[0], TRIPO_QUADRUPED_BONES.head),
  quad_idle: _qManifest('quad_idle', 4, true, 'state'),
  quad_sleep: _qManifest('quad_sleep', 6, true, 'state'),
  quad_eat: _qManifest('quad_eat', 2.5, true, 'daily'),
  quad_walk: _qManifest('quad_walk', 1.2, true, 'locomotion'),
  quad_run: _qManifest('quad_run', 0.8, true, 'locomotion'),
  quad_jump: _qManifest('quad_jump', 1, false, 'locomotion'),
  quad_purr: _qManifest('quad_purr', 3, true, 'interaction'),
  quad_headbutt: _qManifest('quad_headbutt', 1, false, 'interaction'),
  quad_roll: _qManifest('quad_roll', 2, false, 'interaction'),
  quad_happy_wag: _qManifest('quad_happy_wag', 2, true, 'emotion-positive'),
  quad_sad_whine: _qManifest('quad_sad_whine', 3, true, 'emotion-negative'),
  quad_angry_growl: _qManifest('quad_angry_growl', 2.5, true, 'emotion-negative'),
  quad_lap_curl: _qManifest('quad_lap_curl', 4, true, 'intimate'),
  quad_lick_hand: _qManifest('quad_lick_hand', 1.5, false, 'intimate'),
  quad_belly_up: _qManifest('quad_belly_up', 3, true, 'intimate')
}
