import type { ClipDef } from './clips-biped'
import { buildStateClipsForBones, kf, makeClip } from './clips-biped'

interface QuadrupedBoneSlots {
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

const TRIPO_QUADRUPED_BONES: QuadrupedBoneSlots = {
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

const S0 = TRIPO_QUADRUPED_BONES.spine[0]
const S1 = TRIPO_QUADRUPED_BONES.spine[1]
const HD = TRIPO_QUADRUPED_BONES.head
const JW = TRIPO_QUADRUPED_BONES.jaw
const TL0 = TRIPO_QUADRUPED_BONES.tail[0]
const TL1 = TRIPO_QUADRUPED_BONES.tail[1]
const LF = TRIPO_QUADRUPED_BONES.leftFrontLeg[0]
const RF = TRIPO_QUADRUPED_BONES.rightFrontLeg[0]
const LH = TRIPO_QUADRUPED_BONES.leftHindLeg[0]
const RH = TRIPO_QUADRUPED_BONES.rightHindLeg[0]

export const QUADRUPED_CLIPS: Readonly<Record<string, ClipDef>> = {
  ...buildStateClipsForBones(S0, HD),

  // ── States & Locomotion ──
  quad_idle: makeClip('quad_idle', 4, true, 'state', ['温顺', '沉稳', '忠诚'], {
    [S0]: [kf(0, 0, 0, 0), kf(2, 0.02, 0, 0), kf(4, 0, 0, 0)],
    [HD]: [kf(0, 0, 0, 0), kf(2, 0.03, 0.02, 0), kf(4, 0, 0, 0)],
    [TL0]: [kf(0, 0, 0, 0), kf(2, 0, 0.05, 0), kf(4, 0, 0, 0)]
  }),
  quad_sleep: makeClip('quad_sleep', 6, true, 'state', ['温顺', '懒散', '安详'], {
    [S0]: [kf(0, -0.05, 0, 0), kf(3, -0.08, 0, 0), kf(6, -0.05, 0, 0)],
    [HD]: [kf(0, 0.3, 0.1, 0), kf(3, 0.35, 0.12, 0), kf(6, 0.3, 0.1, 0)],
    [TL0]: [kf(0, -0.1, 0, 0), kf(3, -0.1, 0.02, 0), kf(6, -0.1, 0, 0)]
  }),
  quad_walk: makeClip('quad_walk', 1.2, true, 'locomotion', ['欢腾', '机敏', '敏捷'], {
    [S0]: [kf(0, 0, 0.02, 0), kf(0.6, 0, -0.02, 0), kf(1.2, 0, 0.02, 0)],
    [LF]: [kf(0, 0.3, 0, 0), kf(0.6, -0.2, 0, 0), kf(1.2, 0.3, 0, 0)],
    [RF]: [kf(0, -0.2, 0, 0), kf(0.6, 0.3, 0, 0), kf(1.2, -0.2, 0, 0)],
    [LH]: [kf(0, -0.2, 0, 0), kf(0.6, 0.3, 0, 0), kf(1.2, -0.2, 0, 0)],
    [RH]: [kf(0, 0.3, 0, 0), kf(0.6, -0.2, 0, 0), kf(1.2, 0.3, 0, 0)],
    [TL0]: [kf(0, 0, 0.1, 0), kf(0.6, 0, -0.1, 0), kf(1.2, 0, 0.1, 0)]
  }),
  quad_run: makeClip('quad_run', 0.6, true, 'locomotion', ['精力充沛', '狂野', '敏捷'], {
    [S0]: [kf(0, -0.1, 0, 0), kf(0.3, 0.15, 0, 0), kf(0.6, -0.1, 0, 0)],
    [LF]: [kf(0, 0.6, 0, 0), kf(0.3, -0.5, 0, 0), kf(0.6, 0.6, 0, 0)],
    [RF]: [kf(0, 0.5, 0, 0), kf(0.3, -0.4, 0, 0), kf(0.6, 0.5, 0, 0)],
    [LH]: [kf(0, -0.6, 0, 0), kf(0.3, 0.7, 0, 0), kf(0.6, -0.6, 0, 0)],
    [RH]: [kf(0, -0.5, 0, 0), kf(0.3, 0.6, 0, 0), kf(0.6, -0.5, 0, 0)],
    [TL0]: [kf(0, 0.2, 0, 0), kf(0.3, 0.3, 0, 0), kf(0.6, 0.2, 0, 0)]
  }),
  quad_jump: makeClip('quad_jump', 1.0, false, 'locomotion', ['精力充沛', '活泼', '狂野'], {
    [S0]: [kf(0, 0, 0, 0), kf(0.3, -0.3, 0, 0), kf(0.6, 0.2, 0, 0), kf(1.0, 0, 0, 0)],
    [LF]: [kf(0, 0, 0, 0), kf(0.3, -0.4, 0, 0), kf(0.6, 0.5, 0, 0), kf(1.0, 0, 0, 0)],
    [RF]: [kf(0, 0, 0, 0), kf(0.3, -0.4, 0, 0), kf(0.6, 0.5, 0, 0), kf(1.0, 0, 0, 0)],
    [LH]: [kf(0, 0, 0, 0), kf(0.3, 0.5, 0, 0), kf(0.6, -0.4, 0, 0), kf(1.0, 0, 0, 0)],
    [RH]: [kf(0, 0, 0, 0), kf(0.3, 0.5, 0, 0), kf(0.6, -0.4, 0, 0), kf(1.0, 0, 0, 0)]
  }),
  quad_stalk: makeClip('quad_stalk', 2.0, true, 'locomotion', ['捕猎', '警惕', '机警敏捷'], {
    [S0]: [kf(0, 0.05, 0, 0), kf(1.0, 0.08, 0, 0), kf(2.0, 0.05, 0, 0)],
    [HD]: [kf(0, -0.15, 0, 0), kf(1.0, -0.18, 0, 0), kf(2.0, -0.15, 0, 0)],
    [TL0]: [kf(0, -0.2, 0, 0), kf(1.0, -0.2, 0.05, 0), kf(2.0, -0.2, 0, 0)]
  }),
  quad_pounce: makeClip('quad_pounce', 1.2, false, 'locomotion', ['捕猎', '狂野', '敏捷'], {
    [S0]: [kf(0, 0.1, 0, 0), kf(0.4, -0.25, 0, 0), kf(0.8, 0.3, 0, 0), kf(1.2, 0, 0, 0)],
    [HD]: [kf(0, 0, 0, 0), kf(0.4, 0.2, 0, 0), kf(0.8, -0.1, 0, 0), kf(1.2, 0, 0, 0)]
  }),
  quad_trot: makeClip('quad_trot', 0.9, true, 'locomotion', ['欢腾', '轻快', '开朗'], {
    [S0]: [kf(0, 0.02, 0, 0), kf(0.45, -0.02, 0, 0), kf(0.9, 0.02, 0, 0)],
    [HD]: [kf(0, 0.05, 0, 0), kf(0.45, 0.08, 0, 0), kf(0.9, 0.05, 0, 0)],
    [TL0]: [kf(0, 0.1, 0.1, 0), kf(0.45, 0.1, -0.1, 0), kf(0.9, 0.1, 0.1, 0)]
  }),

  // ── Daily & Micro ──
  quad_eat: makeClip('quad_eat', 2.5, true, 'daily', ['贪吃', '憨厚', '温顺'], {
    [HD]: [kf(0, 0.4, 0, 0), kf(0.6, 0.45, 0, 0), kf(1.2, 0.38, 0, 0), kf(1.8, 0.46, 0, 0), kf(2.5, 0.4, 0, 0)],
    [JW]: [kf(0, 0, 0, 0), kf(0.6, 0.2, 0, 0), kf(1.2, 0, 0, 0), kf(1.8, 0.25, 0, 0), kf(2.5, 0, 0, 0)]
  }),
  quad_drink: makeClip('quad_drink', 2.0, true, 'daily', ['温顺', '乖巧'], {
    [HD]: [kf(0, 0.42, 0, 0), kf(0.5, 0.45, 0, 0), kf(1.0, 0.42, 0, 0), kf(1.5, 0.46, 0, 0), kf(2.0, 0.42, 0, 0)],
    [JW]: [kf(0, 0.1, 0, 0), kf(0.5, 0.2, 0, 0), kf(1.0, 0.1, 0, 0), kf(1.5, 0.2, 0, 0), kf(2.0, 0.1, 0, 0)]
  }),
  quad_scratch_ear: makeClip('quad_scratch_ear', 2.0, true, 'daily', ['憨厚', '可爱'], {
    [HD]: [kf(0, 0, 0, 0.2), kf(1.0, 0, 0, 0.25), kf(2.0, 0, 0, 0.2)],
    [RH]: [kf(0, 0.4, 0, 0), kf(0.3, 0.7, 0, 0.2), kf(0.6, 0.4, 0, 0), kf(0.9, 0.7, 0, 0.2), kf(2.0, 0.4, 0, 0)]
  }),
  quad_shake_fur: makeClip('quad_shake_fur', 1.5, false, 'daily', ['活泼', '欢腾'], {
    [S0]: [kf(0, 0, 0, 0), kf(0.3, 0, 0.3, 0), kf(0.6, 0, -0.3, 0), kf(0.9, 0, 0.2, 0), kf(1.5, 0, 0, 0)],
    [HD]: [kf(0, 0, 0, 0), kf(0.3, 0, -0.4, 0), kf(0.6, 0, 0.4, 0), kf(0.9, 0, -0.3, 0), kf(1.5, 0, 0, 0)],
    [TL0]: [kf(0, 0, 0, 0), kf(0.3, 0, 0.5, 0), kf(0.6, 0, -0.5, 0), kf(1.5, 0, 0, 0)]
  }),
  quad_stretch_front: makeClip('quad_stretch_front', 2.5, false, 'daily', ['惬意', '慵懒'], {
    [S0]: [kf(0, 0, 0, 0), kf(1.2, 0.2, 0, 0), kf(2.5, 0, 0, 0)],
    [LF]: [kf(0, 0, 0, 0), kf(1.2, 0.5, 0, 0), kf(2.5, 0, 0, 0)],
    [RF]: [kf(0, 0, 0, 0), kf(1.2, 0.5, 0, 0), kf(2.5, 0, 0, 0)],
    [TL0]: [kf(0, 0, 0, 0), kf(1.2, 0.3, 0, 0), kf(2.5, 0, 0, 0)]
  }),
  quad_stretch_hind: makeClip('quad_stretch_hind', 2.0, false, 'daily', ['惬意', '慵懒'], {
    [LH]: [kf(0, 0, 0, 0), kf(1.0, -0.4, 0, 0), kf(2.0, 0, 0, 0)],
    [RH]: [kf(0, 0, 0, 0), kf(1.0, -0.4, 0, 0), kf(2.0, 0, 0, 0)]
  }),
  quad_yawn: makeClip('quad_yawn', 2.5, false, 'daily', ['懒散', '呆萌'], {
    [HD]: [kf(0, 0, 0, 0), kf(1.0, -0.15, 0, 0), kf(2.5, 0, 0, 0)],
    [JW]: [kf(0, 0, 0, 0), kf(1.0, 0.4, 0, 0), kf(2.5, 0, 0, 0)]
  }),
  quad_pant_happy: makeClip('quad_pant_happy', 1.5, true, 'daily', ['元气', '阳光', '欢腾'], {
    [HD]: [kf(0, 0.05, 0, 0), kf(0.75, 0.08, 0, 0), kf(1.5, 0.05, 0, 0)],
    [JW]: [kf(0, 0.15, 0, 0), kf(0.75, 0.25, 0, 0), kf(1.5, 0.15, 0, 0)],
    [TL0]: [kf(0, 0.1, 0.2, 0), kf(0.75, 0.1, -0.2, 0), kf(1.5, 0.1, 0.2, 0)]
  }),
  quad_chew_toy: makeClip('quad_chew_toy', 2.0, true, 'daily', ['贪玩', '拆家', '活泼'], {
    [HD]: [kf(0, 0.2, 0, 0), kf(0.5, 0.25, 0.1, 0), kf(1.0, 0.2, 0, 0), kf(1.5, 0.25, -0.1, 0), kf(2.0, 0.2, 0, 0)],
    [JW]: [kf(0, 0, 0, 0), kf(0.5, 0.3, 0, 0), kf(1.0, 0, 0, 0), kf(1.5, 0.3, 0, 0), kf(2.0, 0, 0, 0)]
  }),
  quad_dig: makeClip('quad_dig', 1.8, true, 'daily', ['拆家', '精力充沛', '贪玩'], {
    [LF]: [kf(0, 0.2, 0, 0), kf(0.3, -0.4, 0, 0), kf(0.6, 0.2, 0, 0)],
    [RF]: [kf(0, -0.4, 0, 0), kf(0.3, 0.2, 0, 0), kf(0.6, -0.4, 0, 0)],
    [HD]: [kf(0, 0.3, 0, 0), kf(0.9, 0.35, 0, 0), kf(1.8, 0.3, 0, 0)]
  }),

  // ── Interaction & Intimate ──
  quad_purr: makeClip('quad_purr', 3.0, true, 'interaction', ['撒娇', '温顺可爱', '亲人'], {
    [S0]: [kf(0, 0.02, 0, 0), kf(1.5, 0.04, 0, 0), kf(3.0, 0.02, 0, 0)],
    [HD]: [kf(0, 0.05, 0.05, 0.02), kf(1.5, 0.08, 0.07, 0.04), kf(3.0, 0.05, 0.05, 0.02)],
    [TL0]: [kf(0, 0.05, 0.05, 0), kf(1.5, 0.05, -0.05, 0), kf(3.0, 0.05, 0.05, 0)]
  }),
  quad_headbutt: makeClip('quad_headbutt', 1.5, false, 'interaction', ['撒娇', '亲人', '爱抚'], {
    [HD]: [kf(0, 0, 0, 0), kf(0.6, 0.1, 0.25, 0.1), kf(1.5, 0, 0, 0)],
    [S0]: [kf(0, 0, 0, 0), kf(0.6, 0, 0.08, 0), kf(1.5, 0, 0, 0)]
  }),
  quad_roll: makeClip('quad_roll', 2.5, false, 'interaction', ['撒娇', '温顺可爱', '贪玩'], {
    [S0]: [kf(0, 0, 0, 0), kf(1.2, 0, 0, 1.2), kf(2.5, 0, 0, 0)],
    [TL0]: [kf(0, 0, 0, 0), kf(1.2, 0, 0.4, 0), kf(2.5, 0, 0, 0)]
  }),
  quad_belly_up: makeClip('quad_belly_up', 3.5, true, 'intimate', ['顺从', '撒娇', '温顺可爱'], {
    [S0]: [kf(0, 0, 0, 1.5), kf(1.75, 0.02, 0, 1.55), kf(3.5, 0, 0, 1.5)],
    [LF]: [kf(0, 0.3, 0, 0.2), kf(1.75, 0.35, 0, 0.25), kf(3.5, 0.3, 0, 0.2)],
    [RF]: [kf(0, 0.3, 0, -0.2), kf(1.75, 0.35, 0, -0.25), kf(3.5, 0.3, 0, -0.2)],
    [TL0]: [kf(0, 0, 0.2, 0), kf(1.75, 0, -0.2, 0), kf(3.5, 0, 0.2, 0)]
  }),
  quad_lick_hand: makeClip('quad_lick_hand', 1.8, false, 'intimate', ['亲人', '爱抚', '忠诚'], {
    [HD]: [kf(0, 0, 0, 0), kf(0.5, 0.15, 0, 0), kf(1.2, 0.2, 0, 0), kf(1.8, 0, 0, 0)],
    [JW]: [kf(0, 0, 0, 0), kf(0.5, 0.2, 0, 0), kf(0.8, 0, 0, 0), kf(1.2, 0.25, 0, 0), kf(1.8, 0, 0, 0)]
  }),
  quad_lap_curl: makeClip('quad_lap_curl', 4.0, true, 'intimate', ['温顺', '粘人', '体贴'], {
    [S0]: [kf(0, 0, 0.3, 0), kf(2.0, 0.02, 0.35, 0), kf(4.0, 0, 0.3, 0)],
    [HD]: [kf(0, 0.2, 0.2, 0), kf(2.0, 0.25, 0.22, 0), kf(4.0, 0.2, 0.2, 0)],
    [TL0]: [kf(0, 0, 0.6, 0), kf(2.0, 0, 0.65, 0), kf(4.0, 0, 0.6, 0)]
  }),
  quad_nudge: makeClip('quad_nudge', 1.5, false, 'interaction', ['撒娇', '亲人', '体贴'], {
    [HD]: [kf(0, 0, 0, 0), kf(0.5, -0.1, 0.1, 0), kf(1.0, 0.1, 0.15, 0), kf(1.5, 0, 0, 0)]
  }),
  quad_paw_touch: makeClip('quad_paw_touch', 1.5, false, 'interaction', ['撒娇', '护主', '亲人'], {
    [LF]: [kf(0, 0, 0, 0), kf(0.6, 0.6, 0, 0.1), kf(1.5, 0, 0, 0)],
    [HD]: [kf(0, 0, 0, 0), kf(0.6, 0.05, 0.05, 0), kf(1.5, 0, 0, 0)]
  }),
  quad_chin_rest: makeClip('quad_chin_rest', 3.0, true, 'intimate', ['憨厚', '温顺', '顺从'], {
    [HD]: [kf(0, 0.25, 0, 0), kf(1.5, 0.28, 0, 0), kf(3.0, 0.25, 0, 0)],
    [S0]: [kf(0, 0.05, 0, 0), kf(1.5, 0.07, 0, 0), kf(3.0, 0.05, 0, 0)]
  }),
  quad_sniff_hand: makeClip('quad_sniff_hand', 1.8, false, 'interaction', ['好奇', '敏锐', '警惕'], {
    [HD]: [kf(0, 0, 0, 0), kf(0.4, 0.1, 0.1, 0), kf(0.9, 0.12, -0.1, 0), kf(1.4, 0.1, 0.05, 0), kf(1.8, 0, 0, 0)]
  }),
  quad_beg: makeClip('quad_beg', 2.5, true, 'interaction', ['贪吃', '撒娇', '温顺可爱'], {
    [S0]: [kf(0, -0.5, 0, 0), kf(1.25, -0.55, 0, 0), kf(2.5, -0.5, 0, 0)],
    [LF]: [kf(0, 0.8, 0, 0.2), kf(1.25, 0.9, 0, 0.25), kf(2.5, 0.8, 0, 0.2)],
    [RF]: [kf(0, 0.8, 0, -0.2), kf(1.25, 0.9, 0, -0.25), kf(2.5, 0.8, 0, -0.2)],
    [HD]: [kf(0, 0.3, 0, 0), kf(1.25, 0.35, 0, 0), kf(2.5, 0.3, 0, 0)]
  }),

  // ── Positive Emotion ──
  quad_happy_wag: makeClip('quad_happy_wag', 1.0, true, 'emotion-positive', ['摇尾', '欢腾', '元气', '开朗'], {
    [TL0]: [kf(0, 0.1, 0.4, 0), kf(0.5, 0.1, -0.4, 0), kf(1.0, 0.1, 0.4, 0)],
    [TL1]: [kf(0, 0, 0.5, 0), kf(0.5, 0, -0.5, 0), kf(1.0, 0, 0.5, 0)],
    [HD]: [kf(0, 0.02, 0.05, 0), kf(0.5, 0.02, -0.05, 0), kf(1.0, 0.02, 0.05, 0)]
  }),
  quad_play_bow: makeClip('quad_play_bow', 2.0, false, 'emotion-positive', ['贪玩', '欢腾', '精力充沛'], {
    [S0]: [kf(0, 0, 0, 0), kf(0.8, 0.4, 0, 0), kf(2.0, 0, 0, 0)],
    [LF]: [kf(0, 0, 0, 0), kf(0.8, 0.5, 0, 0), kf(2.0, 0, 0, 0)],
    [RF]: [kf(0, 0, 0, 0), kf(0.8, 0.5, 0, 0), kf(2.0, 0, 0, 0)],
    [TL0]: [kf(0, 0, 0, 0), kf(0.8, 0.4, 0.3, 0), kf(2.0, 0, 0, 0)]
  }),
  quad_zoomies: makeClip('quad_zoomies', 1.0, true, 'emotion-positive', ['狂野', '精力充沛', '欢腾'], {
    [S0]: [kf(0, -0.1, 0.2, 0), kf(0.5, 0.1, -0.2, 0), kf(1.0, -0.1, 0.2, 0)],
    [TL0]: [kf(0, 0.3, 0.4, 0), kf(0.5, 0.3, -0.4, 0), kf(1.0, 0.3, 0.4, 0)]
  }),
  quad_curious_tilt: makeClip('quad_curious_tilt', 2.0, false, 'emotion-positive', ['好奇', '呆萌', '灵动'], {
    [HD]: [kf(0, 0, 0, 0), kf(0.6, 0.05, 0.1, 0.35), kf(1.4, 0.05, -0.1, -0.3), kf(2.0, 0, 0, 0)]
  }),
  quad_proud_chest: makeClip('quad_proud_chest', 2.5, true, 'emotion-positive', ['高贵', '威严', '护主'], {
    [S0]: [kf(0, -0.1, 0, 0), kf(1.25, -0.15, 0, 0), kf(2.5, -0.1, 0, 0)],
    [HD]: [kf(0, -0.1, 0, 0), kf(1.25, -0.15, 0, 0), kf(2.5, -0.1, 0, 0)],
    [TL0]: [kf(0, 0.2, 0, 0), kf(1.25, 0.25, 0, 0), kf(2.5, 0.2, 0, 0)]
  }),
  quad_excited_hop: makeClip('quad_excited_hop', 1.2, true, 'emotion-positive', ['元气', '开朗', '欢腾'], {
    [LF]: [kf(0, 0.3, 0, 0), kf(0.6, 0, 0, 0), kf(1.2, 0.3, 0, 0)],
    [RF]: [kf(0, 0.3, 0, 0), kf(0.6, 0, 0, 0), kf(1.2, 0.3, 0, 0)],
    [TL0]: [kf(0, 0.3, 0.3, 0), kf(0.6, 0.3, -0.3, 0), kf(1.2, 0.3, 0.3, 0)]
  }),

  // ── Negative Emotion & Guard ──
  quad_sad_whine: makeClip('quad_sad_whine', 3.0, true, 'emotion-negative', ['胆小', '顺从', '惹人怜爱'], {
    [HD]: [kf(0, 0.2, 0, 0), kf(1.5, 0.28, 0, 0), kf(3.0, 0.2, 0, 0)],
    [TL0]: [kf(0, -0.3, 0, 0), kf(1.5, -0.35, 0, 0), kf(3.0, -0.3, 0, 0)],
    [S0]: [kf(0, 0.05, 0, 0), kf(1.5, 0.08, 0, 0), kf(3.0, 0.05, 0, 0)]
  }),
  quad_angry_growl: makeClip('quad_angry_growl', 2.0, true, 'emotion-negative', ['凶猛', '护食', '警戒', '暴躁'], {
    [S0]: [kf(0, 0.08, 0, 0), kf(1.0, 0.12, 0, 0), kf(2.0, 0.08, 0, 0)],
    [HD]: [kf(0, -0.1, 0, 0), kf(1.0, -0.15, 0, 0), kf(2.0, -0.1, 0, 0)],
    [JW]: [kf(0, 0.05, 0, 0), kf(1.0, 0.15, 0, 0), kf(2.0, 0.05, 0, 0)],
    [TL0]: [kf(0, 0.15, 0, 0), kf(1.0, 0.18, 0, 0), kf(2.0, 0.15, 0, 0)]
  }),
  quad_bark_warning: makeClip('quad_bark_warning', 1.0, false, 'emotion-negative', ['警戒', '护主', '领地意识'], {
    [HD]: [kf(0, 0, 0, 0), kf(0.2, -0.2, 0, 0), kf(0.5, 0.1, 0, 0), kf(1.0, 0, 0, 0)],
    [JW]: [kf(0, 0, 0, 0), kf(0.2, 0.35, 0, 0), kf(0.5, 0, 0, 0), kf(1.0, 0, 0, 0)]
  }),
  quad_scared_cower: makeClip('quad_scared_cower', 2.5, true, 'emotion-negative', ['胆小', '敏感', '顺从'], {
    [S0]: [kf(0, 0.15, 0, 0), kf(1.25, 0.2, 0, 0), kf(2.5, 0.15, 0, 0)],
    [HD]: [kf(0, 0.25, 0, 0), kf(1.25, 0.3, 0, 0), kf(2.5, 0.25, 0, 0)],
    [TL0]: [kf(0, -0.4, 0, 0), kf(1.25, -0.45, 0, 0), kf(2.5, -0.4, 0, 0)]
  }),
  quad_guard_pose: makeClip('quad_guard_pose', 3.0, true, 'emotion-negative', ['护主', '威严', '警戒', '忠诚'], {
    [S0]: [kf(0, -0.05, 0, 0), kf(1.5, -0.08, 0, 0), kf(3.0, -0.05, 0, 0)],
    [HD]: [kf(0, 0, 0, 0), kf(1.5, 0.05, 0.1, 0), kf(3.0, 0, 0, 0)],
    [TL0]: [kf(0, 0.2, 0, 0), kf(1.5, 0.25, 0, 0), kf(3.0, 0.2, 0, 0)]
  }),
  quad_howl: makeClip('quad_howl', 3.0, false, 'social', ['狂野', '威严', '神秘'], {
    [HD]: [kf(0, 0, 0, 0), kf(1.0, -0.6, 0, 0), kf(2.2, -0.65, 0, 0), kf(3.0, 0, 0, 0)],
    [JW]: [kf(0, 0, 0, 0), kf(1.0, 0.4, 0, 0), kf(2.2, 0.45, 0, 0), kf(3.0, 0, 0, 0)],
    [S0]: [kf(0, 0, 0, 0), kf(1.0, -0.15, 0, 0), kf(3.0, 0, 0, 0)]
  }),
  quad_alert_stand: makeClip('quad_alert_stand', 2.5, true, 'social', ['警惕', '机敏', '敏锐'], {
    [HD]: [kf(0, -0.1, 0.1, 0), kf(1.25, -0.12, -0.1, 0), kf(2.5, -0.1, 0.1, 0)],
    [TL0]: [kf(0, 0.2, 0, 0), kf(1.25, 0.25, 0, 0), kf(2.5, 0.2, 0, 0)]
  }),
  quad_greeting_rub: makeClip('quad_greeting_rub', 2.5, false, 'ritual', ['温柔', '亲人', '忠诚'], {
    [S0]: [kf(0, 0, 0, 0), kf(1.0, 0, 0.15, 0), kf(2.5, 0, 0, 0)],
    [HD]: [kf(0, 0, 0, 0), kf(1.0, 0.1, 0.2, 0.1), kf(2.5, 0, 0, 0)],
    [TL0]: [kf(0, 0.1, 0.2, 0), kf(1.0, 0.1, -0.2, 0), kf(2.5, 0.1, 0.2, 0)]
  })
}
