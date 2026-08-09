import type { ClipDef } from './clips-biped'
import { buildStateClipsForBones } from './clips-biped'

export interface AquaticBoneSlots {
  root: string
  spine: readonly string[]
  head: string
  jaw: string
  topFin: readonly string[]
  bottomFin: readonly string[]
  sideFin: readonly string[]
  tail: readonly string[]
}

export const TRIPO_AQUATIC_BONES: AquaticBoneSlots = {
  root: 'Hips',
  spine: ['Spine', 'Spine1', 'Spine2', 'Neck'],
  head: 'Head',
  jaw: 'Jaw',
  topFin: ['TopFin'],
  bottomFin: ['BottomFin'],
  sideFin: ['LeftFin', 'RightFin'],
  tail: ['Tail1', 'Tail2', 'Tail3', 'Tail4']
}

function _aqManifest(name: string, duration: number, loop: boolean, category: ClipDef['category']): ClipDef {
  return {
    name,
    duration,
    loop,
    category,
    tracks: {
      [TRIPO_AQUATIC_BONES.spine[0]]: [
        { t: 0, r: [0.0, 0.0, 0.08] as const },
        { t: duration / 2, r: [0.0, 0.0, 0.08] as const },
        { t: duration, r: [0.0, 0.0, 0.08] as const }
      ]
    }
  }
}

export const AQUATIC_CLIPS: Readonly<Record<string, ClipDef>> = {
  ...buildStateClipsForBones(TRIPO_AQUATIC_BONES.spine[0], TRIPO_AQUATIC_BONES.head),
  aquatic_idle_swim: _aqManifest('aquatic_idle_swim', 4, true, 'state'),
  aquatic_sleep_drift: _aqManifest('aquatic_sleep_drift', 6, true, 'state'),
  aquatic_alert_dart: _aqManifest('aquatic_alert_dart', 1, false, 'state'),
  aquatic_listen: _aqManifest('aquatic_listen', 3, true, 'state'),
  aquatic_swim_slow: _aqManifest('aquatic_swim_slow', 2, true, 'locomotion'),
  aquatic_swim_fast: _aqManifest('aquatic_swim_fast', 1, true, 'locomotion'),
  aquatic_dive: _aqManifest('aquatic_dive', 2, false, 'locomotion'),
  aquatic_surface: _aqManifest('aquatic_surface', 1.5, false, 'locomotion'),
  aquatic_breach: _aqManifest('aquatic_breach', 2, false, 'locomotion'),
  aquatic_blow_bubbles: _aqManifest('aquatic_blow_bubbles', 2, true, 'interaction'),
  aquatic_follow: _aqManifest('aquatic_follow', 3, true, 'interaction'),
  aquatic_happy_spin: _aqManifest('aquatic_happy_spin', 2, true, 'emotion-positive'),
  aquatic_scared_scatter: _aqManifest('aquatic_scared_scatter', 1.5, true, 'emotion-negative'),
  aquatic_school: _aqManifest('aquatic_school', 3, true, 'social'),
  aquatic_gentle_nudge: _aqManifest('aquatic_gentle_nudge', 1.5, false, 'intimate'),
  aquatic_kiss_fish: _aqManifest('aquatic_kiss_fish', 1, false, 'intimate'),
  aquatic_feeding_frenzy: _aqManifest('aquatic_feeding_frenzy', 2.5, true, 'ritual'),
  aquatic_mating_dance: _aqManifest('aquatic_mating_dance', 3.5, true, 'ritual')
}
