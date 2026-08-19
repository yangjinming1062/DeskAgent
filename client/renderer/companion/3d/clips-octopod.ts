import type { ClipDef } from './clips-biped'
import { buildStateClipsForBones, kf, makeClip } from './clips-biped'

interface OctopodBoneSlots {
  root: string
  body: readonly string[]
  head: string
  jaw: string
  leftLegs: readonly string[]
  rightLegs: readonly string[]
  tail: readonly string[]
}

const TRIPO_OCTOPOD_BONES: OctopodBoneSlots = {
  root: 'Hips',
  body: ['Spine', 'Spine1', 'Spine2', 'Neck'],
  head: 'Head',
  jaw: 'Jaw',
  leftLegs: ['LeftFrontLeg', 'LeftMidFrontLeg', 'LeftMidBackLeg', 'LeftBackLeg'],
  rightLegs: ['RightFrontLeg', 'RightMidFrontLeg', 'RightMidBackLeg', 'RightBackLeg'],
  tail: ['Tail1']
}

const S0 = TRIPO_OCTOPOD_BONES.body[0]
const HD = TRIPO_OCTOPOD_BONES.head
const JW = TRIPO_OCTOPOD_BONES.jaw
const L0 = TRIPO_OCTOPOD_BONES.leftLegs[0]
const L1 = TRIPO_OCTOPOD_BONES.leftLegs[1]
const L2 = TRIPO_OCTOPOD_BONES.leftLegs[2]
const L3 = TRIPO_OCTOPOD_BONES.leftLegs[3]
const R0 = TRIPO_OCTOPOD_BONES.rightLegs[0]
const R1 = TRIPO_OCTOPOD_BONES.rightLegs[1]
const R2 = TRIPO_OCTOPOD_BONES.rightLegs[2]
const R3 = TRIPO_OCTOPOD_BONES.rightLegs[3]

export const OCTOPOD_CLIPS: Readonly<Record<string, ClipDef>> = {
  ...buildStateClipsForBones(S0, HD),

  // ── States & Locomotion ──
  oct_idle_perch: makeClip('oct_idle_perch', 4, true, 'state', ['莫测', '深海潜行', '多智'], {
    [S0]: [kf(0, 0.05, 0, 0), kf(2, 0.08, 0, 0), kf(4, 0.05, 0, 0)],
    [L0]: [kf(0, 0, 0, 0.2), kf(2, 0, 0, 0.25), kf(4, 0, 0, 0.2)],
    [R0]: [kf(0, 0, 0, -0.2), kf(2, 0, 0, -0.25), kf(4, 0, 0, -0.2)]
  }),
  oct_sleep: makeClip('oct_sleep', 6, true, 'state', ['不可名状', '幽暗', '蛰伏'], {
    [S0]: [kf(0, -0.05, 0, 0), kf(3, -0.08, 0, 0), kf(6, -0.05, 0, 0)],
    [L0]: [kf(0, 0.3, 0, 0.4), kf(3, 0.35, 0, 0.45), kf(6, 0.3, 0, 0.4)],
    [R0]: [kf(0, 0.3, 0, -0.4), kf(3, 0.35, 0, -0.45), kf(6, 0.3, 0, -0.4)]
  }),
  oct_crawl: makeClip('oct_crawl', 1.6, true, 'locomotion', ['触手灵动', '怪诞', '多面'], {
    [L0]: [kf(0, 0.4, 0, 0), kf(0.8, -0.3, 0, 0), kf(1.6, 0.4, 0, 0)],
    [R0]: [kf(0, -0.3, 0, 0), kf(0.8, 0.4, 0, 0), kf(1.6, -0.3, 0, 0)],
    [L1]: [kf(0, -0.2, 0, 0), kf(0.8, 0.3, 0, 0), kf(1.6, -0.2, 0, 0)],
    [R1]: [kf(0, 0.3, 0, 0), kf(0.8, -0.2, 0, 0), kf(1.6, 0.3, 0, 0)],
    [S0]: [kf(0, 0, 0.05, 0), kf(0.8, 0, -0.05, 0), kf(1.6, 0, 0.05, 0)]
  }),
  oct_jet_propel: makeClip('oct_jet_propel', 1.0, true, 'locomotion', ['喷墨', '迅捷', '灵动'], {
    [S0]: [kf(0, -0.4, 0, 0), kf(0.5, 0.2, 0, 0), kf(1.0, -0.4, 0, 0)],
    [L0]: [kf(0, -0.6, 0, 0.1), kf(0.5, 0.4, 0, 0.3), kf(1.0, -0.6, 0, 0.1)],
    [R0]: [kf(0, -0.6, 0, -0.1), kf(0.5, 0.4, 0, -0.3), kf(1.0, -0.6, 0, -0.1)]
  }),

  // ── Interaction & Intimate ──
  oct_tentacle_reach: makeClip('oct_tentacle_reach', 1.8, false, 'interaction', ['探知', '好奇', '多智'], {
    [L0]: [kf(0, 0, 0, 0), kf(0.9, 0.8, 0, 0.3), kf(1.8, 0, 0, 0)],
    [R0]: [kf(0, 0, 0, 0), kf(0.9, 0.6, 0, -0.2), kf(1.8, 0, 0, 0)],
    [HD]: [kf(0, 0, 0, 0), kf(0.9, 0.1, 0, 0), kf(1.8, 0, 0, 0)]
  }),
  oct_tentacle_cuddle: makeClip('oct_tentacle_cuddle', 3.5, true, 'intimate', ['缠绕', '粘人', '克苏鲁', '深情'], {
    [L0]: [kf(0, 0.5, 0, 0.4), kf(1.75, 0.55, 0, 0.45), kf(3.5, 0.5, 0, 0.4)],
    [R0]: [kf(0, 0.5, 0, -0.4), kf(1.75, 0.55, 0, -0.45), kf(3.5, 0.5, 0, -0.4)],
    [L1]: [kf(0, 0.4, 0, 0.3), kf(1.75, 0.45, 0, 0.35), kf(3.5, 0.4, 0, 0.3)],
    [R1]: [kf(0, 0.4, 0, -0.3), kf(1.75, 0.45, 0, -0.35), kf(3.5, 0.4, 0, -0.3)],
    [S0]: [kf(0, 0.05, 0, 0), kf(1.75, 0.07, 0, 0), kf(3.5, 0.05, 0, 0)]
  }),
  oct_head_pat_accept: makeClip('oct_head_pat_accept', 2.0, false, 'intimate', ['温顺', '亲人', '撒娇'], {
    [HD]: [kf(0, 0, 0, 0), kf(1.0, 0.1, 0.1, 0), kf(2.0, 0, 0, 0)],
    [L0]: [kf(0, 0, 0, 0), kf(1.0, 0.3, 0, 0.2), kf(2.0, 0, 0, 0)]
  }),

  // ── Positive & Ritual ──
  oct_happy_wave: makeClip('oct_happy_wave', 2.0, true, 'emotion-positive', ['触手灵动', '欢腾', '怪诞', '俏皮'], {
    [L0]: [kf(0, 0.3, 0, 0.5), kf(1.0, 0.3, 0, -0.2), kf(2.0, 0.3, 0, 0.5)],
    [R0]: [kf(0, 0.3, 0, -0.5), kf(1.0, 0.3, 0, 0.2), kf(2.0, 0.3, 0, -0.5)],
    [L1]: [kf(0, 0.2, 0, -0.3), kf(1.0, 0.2, 0, 0.4), kf(2.0, 0.2, 0, -0.3)],
    [R1]: [kf(0, 0.2, 0, 0.3), kf(1.0, 0.2, 0, -0.4), kf(2.0, 0.2, 0, 0.3)]
  }),
  oct_camouflage_pose: makeClip('oct_camouflage_pose', 3.0, true, 'ritual', ['伪装', '莫测', '神秘'], {
    [S0]: [kf(0, -0.1, 0, 0), kf(1.5, -0.12, 0, 0), kf(3.0, -0.1, 0, 0)],
    [L0]: [kf(0, 0.6, 0, 0.6), kf(1.5, 0.62, 0, 0.65), kf(3.0, 0.6, 0, 0.6)],
    [R0]: [kf(0, 0.6, 0, -0.6), kf(1.5, 0.62, 0, -0.65), kf(3.0, 0.6, 0, -0.6)]
  }),

  // ── Negative Emotion & Threat ──
  oct_threat_display: makeClip(
    'oct_threat_display',
    2.5,
    true,
    'emotion-negative',
    ['克苏鲁', '不可名状', '威严', '凶猛'],
    {
      [S0]: [kf(0, -0.2, 0, 0), kf(1.25, -0.25, 0, 0), kf(2.5, -0.2, 0, 0)],
      [L0]: [kf(0, 0.8, 0, 0.8), kf(1.25, 1.0, 0, 1.0), kf(2.5, 0.8, 0, 0.8)],
      [R0]: [kf(0, 0.8, 0, -0.8), kf(1.25, 1.0, 0, -1.0), kf(2.5, 0.8, 0, -0.8)],
      [L1]: [kf(0, 0.7, 0, 0.6), kf(1.25, 0.9, 0, 0.8), kf(2.5, 0.7, 0, 0.6)],
      [R1]: [kf(0, 0.7, 0, -0.6), kf(1.25, 0.9, 0, -0.8), kf(2.5, 0.7, 0, -0.6)]
    }
  ),
  oct_ink_escape: makeClip('oct_ink_escape', 1.2, false, 'emotion-negative', ['喷墨', '诡异', '敏锐'], {
    [S0]: [kf(0, 0, 0, 0), kf(0.4, -0.5, 0, 0), kf(1.2, 0, 0, 0)],
    [L0]: [kf(0, 0, 0, 0), kf(0.4, -0.7, 0, 0.3), kf(1.2, 0, 0, 0)],
    [R0]: [kf(0, 0, 0, 0), kf(0.4, -0.7, 0, -0.3), kf(1.2, 0, 0, 0)]
  })
}
