import type { ClipDef } from './clips-biped'
import { buildStateClipsForBones, kf, makeClip } from './clips-biped'

interface HexapodBoneSlots {
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

const TRIPO_HEXAPOD_BONES: HexapodBoneSlots = {
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

const S0 = TRIPO_HEXAPOD_BONES.body[0]
const HD = TRIPO_HEXAPOD_BONES.head
const JW = TRIPO_HEXAPOD_BONES.jaw
const LA = TRIPO_HEXAPOD_BONES.leftAntenna[0]
const RA = TRIPO_HEXAPOD_BONES.rightAntenna[0]
const LF = TRIPO_HEXAPOD_BONES.leftFront[0]
const LM = TRIPO_HEXAPOD_BONES.leftMid[0]
const LH = TRIPO_HEXAPOD_BONES.leftHind[0]
const RF = TRIPO_HEXAPOD_BONES.rightFront[0]
const RM = TRIPO_HEXAPOD_BONES.rightMid[0]
const RH = TRIPO_HEXAPOD_BONES.rightHind[0]

export const HEXAPOD_CLIPS: Readonly<Record<string, ClipDef>> = {
  ...buildStateClipsForBones(S0, HD),

  // ── 状态与位移 ──
  hex_idle_perch: makeClip('hex_idle_perch', 4, true, 'state', ['秩序', '沉稳', '机械', '甲壳坚硬'], {
    [S0]: [kf(0, 0, 0, 0), kf(2, 0.02, 0, 0), kf(4, 0, 0, 0)],
    [LA]: [kf(0, 0, 0, 0), kf(2, 0.05, 0.08, 0), kf(4, 0, 0, 0)],
    [RA]: [kf(0, 0, 0, 0), kf(2, 0.05, -0.08, 0), kf(4, 0, 0, 0)]
  }),
  hex_sleep: makeClip('hex_sleep', 6, true, 'state', ['蛰伏', '隐忍', '冷酷高效'], {
    [S0]: [kf(0, -0.05, 0, 0), kf(3, -0.07, 0, 0), kf(6, -0.05, 0, 0)],
    [HD]: [kf(0, 0.2, 0, 0), kf(3, 0.22, 0, 0), kf(6, 0.2, 0, 0)],
    [LA]: [kf(0, 0.3, 0, 0), kf(3, 0.35, 0, 0), kf(6, 0.3, 0, 0)],
    [RA]: [kf(0, 0.3, 0, 0), kf(3, 0.35, 0, 0), kf(6, 0.3, 0, 0)]
  }),
  hex_crawl: makeClip('hex_crawl', 1.2, true, 'locomotion', ['秩序', '机械', '勤劳'], {
    // 三足步态：LF + RM + LH 同步移动；RF + LM + RH 同步移动
    [LF]: [kf(0, 0.3, 0, 0), kf(0.6, -0.2, 0, 0), kf(1.2, 0.3, 0, 0)],
    [RM]: [kf(0, 0.3, 0, 0), kf(0.6, -0.2, 0, 0), kf(1.2, 0.3, 0, 0)],
    [LH]: [kf(0, 0.3, 0, 0), kf(0.6, -0.2, 0, 0), kf(1.2, 0.3, 0, 0)],
    [RF]: [kf(0, -0.2, 0, 0), kf(0.6, 0.3, 0, 0), kf(1.2, -0.2, 0, 0)],
    [LM]: [kf(0, -0.2, 0, 0), kf(0.6, 0.3, 0, 0), kf(1.2, -0.2, 0, 0)],
    [RH]: [kf(0, -0.2, 0, 0), kf(0.6, 0.3, 0, 0), kf(1.2, -0.2, 0, 0)],
    [S0]: [kf(0, 0, 0.03, 0), kf(0.6, 0, -0.03, 0), kf(1.2, 0, 0.03, 0)]
  }),
  hex_scuttle: makeClip('hex_scuttle', 0.6, true, 'locomotion', ['迅捷', '敏锐', '探索'], {
    [LF]: [kf(0, 0.4, 0, 0), kf(0.3, -0.3, 0, 0), kf(0.6, 0.4, 0, 0)],
    [RM]: [kf(0, 0.4, 0, 0), kf(0.3, -0.3, 0, 0), kf(0.6, 0.4, 0, 0)],
    [LH]: [kf(0, 0.4, 0, 0), kf(0.3, -0.3, 0, 0), kf(0.6, 0.4, 0, 0)],
    [RF]: [kf(0, -0.3, 0, 0), kf(0.3, 0.4, 0, 0), kf(0.6, -0.3, 0, 0)],
    [LM]: [kf(0, -0.3, 0, 0), kf(0.3, 0.4, 0, 0), kf(0.6, -0.3, 0, 0)],
    [RH]: [kf(0, -0.3, 0, 0), kf(0.3, 0.4, 0, 0), kf(0.6, -0.3, 0, 0)]
  }),

  // ── 交互与小动作 ──
  hex_antenna_explore: makeClip('hex_antenna_explore', 2.0, true, 'interaction', ['触角敏锐', '探索', '好奇'], {
    [LA]: [kf(0, 0, 0.2, 0), kf(0.5, 0.1, -0.2, 0), kf(1.0, 0, 0.3, 0), kf(1.5, 0.1, -0.1, 0), kf(2.0, 0, 0.2, 0)],
    [RA]: [kf(0, 0, -0.2, 0), kf(0.5, 0.1, 0.2, 0), kf(1.0, 0, -0.3, 0), kf(1.5, 0.1, 0.1, 0), kf(2.0, 0, -0.2, 0)],
    [HD]: [kf(0, 0.05, 0, 0), kf(1.0, 0.08, 0, 0), kf(2.0, 0.05, 0, 0)]
  }),
  hex_mandible_click: makeClip('hex_mandible_click', 1.2, true, 'interaction', ['机械', '冷酷高效', '秩序'], {
    [JW]: [
      kf(0, 0, 0, 0),
      kf(0.2, 0.25, 0, 0),
      kf(0.4, 0, 0, 0),
      kf(0.6, 0.25, 0, 0),
      kf(0.8, 0, 0, 0),
      kf(1.2, 0, 0, 0)
    ]
  }),
  hex_clean_antenna: makeClip('hex_clean_antenna', 2.5, false, 'daily', ['严谨', '工蜂', '勤劳'], {
    [LF]: [kf(0, 0, 0, 0), kf(0.8, 0.6, 0, 0.4), kf(1.6, 0.7, 0, 0.3), kf(2.5, 0, 0, 0)],
    [LA]: [kf(0, 0, 0, 0), kf(0.8, 0.3, 0, 0), kf(1.6, 0.2, 0, 0), kf(2.5, 0, 0, 0)],
    [HD]: [kf(0, 0, 0, 0), kf(0.8, 0.1, -0.15, 0), kf(2.5, 0, 0, 0)]
  }),
  hex_build_nest: makeClip('hex_build_nest', 3.5, true, 'daily', ['筑巢', '工蜂', '勤劳', '坚韧'], {
    [LF]: [kf(0, 0.3, 0, 0), kf(0.6, -0.2, 0, 0), kf(1.2, 0.3, 0, 0)],
    [RF]: [kf(0, -0.2, 0, 0), kf(0.6, 0.3, 0, 0), kf(1.2, -0.2, 0, 0)],
    [JW]: [kf(0, 0.1, 0, 0), kf(0.6, 0.3, 0, 0), kf(1.2, 0.1, 0, 0)]
  }),

  // ── 正面情绪与社交 ──
  hex_happy_vibrate: makeClip('hex_happy_vibrate', 1.0, true, 'emotion-positive', ['欢腾', '灵动', '服从'], {
    [S0]: [kf(0, 0, 0.05, 0), kf(0.25, 0, -0.05, 0), kf(0.5, 0, 0.05, 0), kf(0.75, 0, -0.05, 0), kf(1.0, 0, 0.05, 0)],
    [LA]: [kf(0, 0.1, 0, 0), kf(0.5, -0.1, 0, 0), kf(1.0, 0.1, 0, 0)],
    [RA]: [kf(0, 0.1, 0, 0), kf(0.5, -0.1, 0, 0), kf(1.0, 0.1, 0, 0)]
  }),
  hex_mimic_pose: makeClip('hex_mimic_pose', 3.0, true, 'ritual', ['拟态', '神秘', '伪装'], {
    [S0]: [kf(0, 0.1, 0, 0), kf(1.5, 0.12, 0, 0), kf(3.0, 0.1, 0, 0)],
    [LF]: [kf(0, 0.8, 0, 0), kf(1.5, 0.82, 0, 0), kf(3.0, 0.8, 0, 0)],
    [RF]: [kf(0, 0.8, 0, 0), kf(1.5, 0.82, 0, 0), kf(3.0, 0.8, 0, 0)]
  }),
  hex_swarm_sync: makeClip('hex_swarm_sync', 2.0, true, 'social', ['群集', '秩序', '服从'], {
    [LA]: [kf(0, 0, 0.3, 0), kf(1.0, 0, -0.3, 0), kf(2.0, 0, 0.3, 0)],
    [RA]: [kf(0, 0, -0.3, 0), kf(1.0, 0, 0.3, 0), kf(2.0, 0, -0.3, 0)]
  }),

  // ── 负面情绪与威胁 ──
  hex_rage_frenzy: makeClip('hex_rage_frenzy', 1.0, true, 'emotion-negative', ['狂躁', '暴躁', '凶猛'], {
    [JW]: [kf(0, 0, 0, 0), kf(0.2, 0.4, 0, 0), kf(0.4, 0, 0, 0), kf(0.6, 0.4, 0, 0), kf(1.0, 0, 0, 0)],
    [LF]: [kf(0, 0.5, 0, 0), kf(0.5, -0.4, 0, 0), kf(1.0, 0.5, 0, 0)],
    [RF]: [kf(0, -0.4, 0, 0), kf(0.5, 0.5, 0, 0), kf(1.0, -0.4, 0, 0)]
  }),
  hex_defend_curl: makeClip('hex_defend_curl', 2.0, true, 'emotion-negative', ['甲壳坚硬', '坚韧', '蛰伏'], {
    [S0]: [kf(0, 0.4, 0, 0), kf(1.0, 0.45, 0, 0), kf(2.0, 0.4, 0, 0)],
    [HD]: [kf(0, 0.3, 0, 0), kf(1.0, 0.35, 0, 0), kf(2.0, 0.3, 0, 0)]
  })
}
