import type { ClipDef } from './clips-biped'
import { buildStateClipsForBones } from './clips-biped'

export interface SerpentineBoneSlots {
  root: string
  spine: readonly string[]
  head: string
  jaw: string
  tail: readonly string[]
}

export const TRIPO_SERPENTINE_BONES: SerpentineBoneSlots = {
  root: 'Hips',
  spine: ['Spine', 'Spine1', 'Spine2', 'Spine3', 'Spine4', 'Spine5', 'Spine6', 'Spine7', 'Spine8', 'Spine9', 'Neck'],
  head: 'Head',
  jaw: 'Jaw',
  tail: ['Tail1', 'Tail2', 'Tail3', 'Tail4', 'Tail5']
}

function _sManifest(name: string, duration: number, loop: boolean, category: ClipDef['category']): ClipDef {
  return {
    name,
    duration,
    loop,
    category,
    tracks: {
      [TRIPO_SERPENTINE_BONES.spine[0]]: [
        { t: 0, r: [0.02, 0.0, 0.06] as const },
        { t: duration / 2, r: [0.02, 0.0, 0.06] as const },
        { t: duration, r: [0.02, 0.0, 0.06] as const }
      ]
    }
  }
}

export const SERPENTINE_CLIPS: Readonly<Record<string, ClipDef>> = {
  ...buildStateClipsForBones(TRIPO_SERPENTINE_BONES.spine[0], TRIPO_SERPENTINE_BONES.head),
  serpent_idle_coil: _sManifest('serpent_idle_coil', 4, true, 'state'),
  serpent_sleep_coil: _sManifest('serpent_sleep_coil', 6, true, 'state'),
  serpent_alert_raise: _sManifest('serpent_alert_raise', 1.5, false, 'state'),
  serpent_listen: _sManifest('serpent_listen', 3, true, 'state'),
  serpent_slither: _sManifest('serpent_slither', 1.5, true, 'locomotion'),
  serpent_slither_fast: _sManifest('serpent_slither_fast', 0.8, true, 'locomotion'),
  serpent_strike: _sManifest('serpent_strike', 1.2, false, 'locomotion'),
  serpent_coil_tight: _sManifest('serpent_coil_tight', 2.5, false, 'locomotion'),
  serpent_uncoil: _sManifest('serpent_uncoil', 2, false, 'locomotion'),
  serpent_tongue_flick: _sManifest('serpent_tongue_flick', 1.5, true, 'interaction'),
  serpent_hiss: _sManifest('serpent_hiss', 2, false, 'interaction'),
  serpent_angry_hiss: _sManifest('serpent_angry_hiss', 2.5, true, 'emotion-negative'),
  serpent_content_coil: _sManifest('serpent_content_coil', 4, true, 'emotion-positive'),
  serpent_curl_around: _sManifest('serpent_curl_around', 3, true, 'intimate'),
  serpent_shed_skin: _sManifest('serpent_shed_skin', 3, false, 'ritual')
}
