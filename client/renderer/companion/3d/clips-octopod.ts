import type { ClipDef } from './clips-biped'
import { buildStateClipsForBones } from './clips-biped'

export interface OctopodBoneSlots {
  root: string
  body: readonly string[]
  head: string
  jaw: string
  leftLegs: readonly string[]
  rightLegs: readonly string[]
  tail: readonly string[]
}

export const TRIPO_OCTOPOD_BONES: OctopodBoneSlots = {
  root: 'Hips',
  body: ['Spine', 'Spine1', 'Spine2', 'Neck'],
  head: 'Head',
  jaw: 'Jaw',
  leftLegs: ['LeftFrontLeg', 'LeftMidFrontLeg', 'LeftMidBackLeg', 'LeftBackLeg'],
  rightLegs: ['RightFrontLeg', 'RightMidFrontLeg', 'RightMidBackLeg', 'RightBackLeg'],
  tail: ['Tail1']
}

function _oManifest(name: string, duration: number, loop: boolean, category: ClipDef['category']): ClipDef {
  return {
    name,
    duration,
    loop,
    category,
    tracks: {
      [TRIPO_OCTOPOD_BONES.body[0]]: [
        { t: 0, r: [0.03, 0.0, 0.05] as const },
        { t: duration / 2, r: [0.03, 0.0, 0.05] as const },
        { t: duration, r: [0.03, 0.0, 0.05] as const }
      ]
    }
  }
}

export const OCTOPOD_CLIPS: Readonly<Record<string, ClipDef>> = {
  ...buildStateClipsForBones(TRIPO_OCTOPOD_BONES.body[0], TRIPO_OCTOPOD_BONES.head),
  oct_idle_perch: _oManifest('oct_idle_perch', 4, true, 'state'),
  oct_sleep: _oManifest('oct_sleep', 6, true, 'state'),
  oct_alert: _oManifest('oct_alert', 1.5, false, 'state'),
  oct_listen: _oManifest('oct_listen', 3, true, 'state'),
  oct_crawl: _oManifest('oct_crawl', 1.5, true, 'locomotion'),
  oct_scuttle: _oManifest('oct_scuttle', 0.8, true, 'locomotion'),
  oct_climb: _oManifest('oct_climb', 2, true, 'locomotion'),
  oct_jet_propel: _oManifest('oct_jet_propel', 1, true, 'locomotion'),
  oct_tentacle_reach: _oManifest('oct_tentacle_reach', 1.5, false, 'interaction'),
  oct_leg_preen: _oManifest('oct_leg_preen', 3, true, 'interaction'),
  oct_eat: _oManifest('oct_eat', 2.5, true, 'daily'),
  oct_happy_dance: _oManifest('oct_happy_dance', 2, true, 'emotion-positive'),
  oct_scared_hide: _oManifest('oct_scared_hide', 1.5, false, 'emotion-negative'),
  oct_threat_display: _oManifest('oct_threat_display', 2.5, true, 'emotion-negative'),
  oct_tentacle_cuddle: _oManifest('oct_tentacle_cuddle', 3, true, 'intimate'),
  oct_web_spin: _oManifest('oct_web_spin', 4, false, 'ritual'),
  oct_color_change: _oManifest('oct_color_change', 1.5, true, 'ritual')
}
