import type { ClipDef } from './clips-biped'
import { buildStateClipsForBones } from './clips-biped'

export interface HexapodBoneSlots {
  root: string
  body: readonly string[]
  head: string
  jaw: string
  leftAntenna: readonly string[]
  rightAntenna: readonly string[]
  leftFront: readonly string[]
  leftMid: readonly string[]
  leftHind: readonly string[]
  rightFront: readonly string[]
  rightMid: readonly string[]
  rightHind: readonly string[]
  tail: readonly string[]
}

export const TRIPO_HEXAPOD_BONES: HexapodBoneSlots = {
  root: 'Hips',
  body: ['Spine', 'Spine1', 'Spine2', 'Neck'],
  head: 'Head',
  jaw: 'Jaw',
  leftAntenna: ['LeftAntenna'],
  rightAntenna: ['RightAntenna'],
  leftFront: ['LeftFrontLeg', 'LeftFrontKnee', 'LeftFrontFoot'],
  leftMid: ['LeftMidLeg', 'LeftMidKnee', 'LeftMidFoot'],
  leftHind: ['LeftHindLeg', 'LeftHindKnee', 'LeftHindFoot'],
  rightFront: ['RightFrontLeg', 'RightFrontKnee', 'RightFrontFoot'],
  rightMid: ['RightMidLeg', 'RightMidKnee', 'RightMidFoot'],
  rightHind: ['RightHindLeg', 'RightHindKnee', 'RightHindFoot'],
  tail: ['Tail1', 'Tail2']
}

function _hManifest(name: string, duration: number, loop: boolean, category: ClipDef['category']): ClipDef {
  return {
    name,
    duration,
    loop,
    category,
    tracks: {
      [TRIPO_HEXAPOD_BONES.body[0]]: [
        { t: 0, r: [0.04, 0.0, 0.03] as const },
        { t: duration / 2, r: [0.04, 0.0, 0.03] as const },
        { t: duration, r: [0.04, 0.0, 0.03] as const }
      ]
    }
  }
}

export const HEXAPOD_CLIPS: Readonly<Record<string, ClipDef>> = {
  ...buildStateClipsForBones(TRIPO_HEXAPOD_BONES.body[0], TRIPO_HEXAPOD_BONES.head),
  hex_idle_perch: _hManifest('hex_idle_perch', 4, true, 'state'),
  hex_sleep: _hManifest('hex_sleep', 6, true, 'state'),
  hex_alert_antenna: _hManifest('hex_alert_antenna', 1.5, false, 'state'),
  hex_listen: _hManifest('hex_listen', 3, true, 'state'),
  hex_crawl: _hManifest('hex_crawl', 1.5, true, 'locomotion'),
  hex_scuttle: _hManifest('hex_scuttle', 0.8, true, 'locomotion'),
  hex_climb: _hManifest('hex_climb', 2, true, 'locomotion'),
  hex_jump: _hManifest('hex_jump', 1, false, 'locomotion'),
  hex_antenna_wiggle: _hManifest('hex_antenna_wiggle', 2, true, 'interaction'),
  hex_preen_legs: _hManifest('hex_preen_legs', 3, true, 'interaction'),
  hex_eat_leaf: _hManifest('hex_eat_leaf', 2, true, 'daily'),
  hex_happy_wings: _hManifest('hex_happy_wings', 1.5, true, 'emotion-positive'),
  hex_scared_hide: _hManifest('hex_scared_hide', 1.5, false, 'emotion-negative'),
  hex_territorial_puff: _hManifest('hex_territorial_puff', 2.5, true, 'emotion-negative'),
  hex_antenna_caress: _hManifest('hex_antenna_caress', 2, true, 'intimate'),
  hex_metamorphosis_dance: _hManifest('hex_metamorphosis_dance', 3, true, 'ritual'),
  hex_territorial_display: _hManifest('hex_territorial_display', 2.5, true, 'ritual')
}
