import { describe, expect, it } from 'vitest'

import { AQUATIC_CLIPS } from './clips-aquatic'
import { AVIAN_CLIPS } from './clips-avian'
import { BIPED_CLIPS } from './clips-biped'
import { HEXAPOD_CLIPS } from './clips-hexapod'
import { OCTOPOD_CLIPS } from './clips-octopod'
import { QUADRUPED_CLIPS } from './clips-quadruped'
import {
  buildClipsForRig,
  getClipDefs,
  getClipNames,
  summarizeRigLibraries,
  SUPPORTED_RIG_TYPES
} from './clips-registry'
import { SERPENTINE_CLIPS } from './clips-serpentine'

const CANONICAL_STATE_CLIPS = [
  'idle',
  'listening',
  'thinking',
  'speaking',
  'working',
  'sleeping',
  'interacting',
  'emotional_idle',
  'disconnected'
] as const

describe('clips-registry', () => {
  it('列出全部 7 种骨骼类型', () => {
    expect(SUPPORTED_RIG_TYPES).toEqual(['biped', 'quadruped', 'avian', 'serpentine', 'aquatic', 'hexapod', 'octopod'])
  })

  it.each([
    ['biped', BIPED_CLIPS],
    ['quadruped', QUADRUPED_CLIPS],
    ['avian', AVIAN_CLIPS],
    ['serpentine', SERPENTINE_CLIPS],
    ['aquatic', AQUATIC_CLIPS],
    ['hexapod', HEXAPOD_CLIPS],
    ['octopod', OCTOPOD_CLIPS]
  ])('为 %s 提供标准状态 clip', (rig, defs) => {
    // AnimationMap.resolveClip 用这些精确名字查找状态；缺一个就让该角色无法动画该状态。
    for (const state of CANONICAL_STATE_CLIPS) {
      expect(defs, `${rig} missing canonical clip '${state}'`).toHaveProperty(state)
    }
  })

  it.each([
    ['biped', BIPED_CLIPS],
    ['quadruped', QUADRUPED_CLIPS],
    ['avian', AVIAN_CLIPS],
    ['serpentine', SERPENTINE_CLIPS],
    ['aquatic', AQUATIC_CLIPS],
    ['hexapod', HEXAPOD_CLIPS],
    ['octopod', OCTOPOD_CLIPS]
  ])('returns the matching library for %s', (rig, expected) => {
    expect(getClipDefs(rig)).toBe(expected)
  })

  it('未知骨骼类型时回退到 biped', () => {
    expect(getClipDefs('avian-but-spelled-wrong')).toBe(BIPED_CLIPS)
    expect(getClipDefs(null)).toBe(BIPED_CLIPS)
    expect(getClipDefs('')).toBe(BIPED_CLIPS)
  })

  it('不支持的骨骼类型时回退到 biped', () => {
    expect(getClipDefs('mech')).toBe(BIPED_CLIPS)
  })

  it.each([
    ['biped', Object.keys(BIPED_CLIPS).length],
    ['quadruped', Object.keys(QUADRUPED_CLIPS).length],
    ['avian', Object.keys(AVIAN_CLIPS).length],
    ['serpentine', Object.keys(SERPENTINE_CLIPS).length],
    ['aquatic', Object.keys(AQUATIC_CLIPS).length],
    ['hexapod', Object.keys(HEXAPOD_CLIPS).length],
    ['octopod', Object.keys(OCTOPOD_CLIPS).length]
  ])('为 %s 构建 THREE.AnimationClip 实例', (rig, expectedCount) => {
    const clips = buildClipsForRig(rig)
    expect(clips.length).toBe(expectedCount)

    for (const clip of clips) {
      expect(clip.name).toBeTruthy()
      expect(clip.duration).toBeGreaterThan(0)
    }
  })

  it('汇总每个骨骼库，给出精确数量', () => {
    const summary = summarizeRigLibraries()
    expect(summary.length).toBe(7)
    const counts = Object.fromEntries(summary.map(s => [s.rig_type, s.count]))
    expect(counts).toEqual({
      biped: Object.keys(BIPED_CLIPS).length,
      quadruped: Object.keys(QUADRUPED_CLIPS).length,
      avian: Object.keys(AVIAN_CLIPS).length,
      serpentine: Object.keys(SERPENTINE_CLIPS).length,
      aquatic: Object.keys(AQUATIC_CLIPS).length,
      hexapod: Object.keys(HEXAPOD_CLIPS).length,
      octopod: Object.keys(OCTOPOD_CLIPS).length
    })
  })

  it('按骨骼类型暴露 clip 名称', () => {
    expect(getClipNames('biped')).toContain('idle')
    expect(getClipNames('biped')).toContain('comfort_pat')
    expect(getClipNames('quadruped')).toContain('quad_idle')
    expect(getClipNames('avian')).toContain('avian_fly_flap')
    expect(getClipNames('serpentine')).toContain('serpent_slither')
    expect(getClipNames('aquatic')).toContain('aquatic_swim_slow')
    expect(getClipNames('hexapod')).toContain('hex_crawl')
    expect(getClipNames('octopod')).toContain('oct_jet_propel')
  })

  it.each([
    ['biped', BIPED_CLIPS],
    ['quadruped', QUADRUPED_CLIPS],
    ['avian', AVIAN_CLIPS],
    ['serpentine', SERPENTINE_CLIPS],
    ['aquatic', AQUATIC_CLIPS],
    ['hexapod', HEXAPOD_CLIPS],
    ['octopod', OCTOPOD_CLIPS]
  ])('确保 %s 里的 clip 拥有合法的定义与 tags', (rig, defs) => {
    for (const [name, clip] of Object.entries(defs)) {
      expect(clip.name).toBe(name)
      expect(clip.duration).toBeGreaterThan(0)

      if (clip.tags) {
        expect(Array.isArray(clip.tags)).toBe(true)
        expect(clip.tags.length).toBeGreaterThan(0)

        for (const t of clip.tags) {
          expect(typeof t).toBe('string')
          expect(t.length).toBeGreaterThan(0)
        }
      }
    }
  })

  it('确保 biped idle clip 提供自然站姿而不是僵硬的 A pose', () => {
    const idle = BIPED_CLIPS.idle
    expect(idle).toBeDefined()
    expect(idle.tracks).toHaveProperty('LeftArm')
    expect(idle.tracks).toHaveProperty('RightArm')
    expect(idle.tracks).toHaveProperty('LeftForeArm')
    expect(idle.tracks).toHaveProperty('RightForeArm')
    expect(idle.tracks).toHaveProperty('Spine')
    expect(idle.tracks).toHaveProperty('Head')

    // Mixamo 静息角下左右上臂需用相反符号的 X 向躯干外侧展开。
    for (const kf of idle.tracks.LeftArm) {
      expect(kf.r[0]).toBeLessThanOrEqual(-0.08)
    }

    for (const kf of idle.tracks.RightArm) {
      expect(kf.r[0]).toBeGreaterThanOrEqual(0.08)
    }

    // 前臂应有自然的肘部弯曲（> 0.1 rad）
    for (const kf of idle.tracks.LeftForeArm) {
      expect(kf.r[0]).toBeGreaterThan(0.1)
    }

    for (const kf of idle.tracks.RightForeArm) {
      expect(kf.r[0]).toBeGreaterThan(0.1)
    }
  })

  it('确保 biped 占位 clip 包含自然的静息手臂轨迹，避免 A pose 跳变', () => {
    const placeholder = BIPED_CLIPS.idle_yawn
    expect(placeholder).toBeDefined()
    expect(placeholder.tracks).toHaveProperty('LeftArm')
    expect(placeholder.tracks).toHaveProperty('RightArm')
    expect(placeholder.tracks).toHaveProperty('LeftForeArm')
    expect(placeholder.tracks).toHaveProperty('RightForeArm')

    for (const kf of placeholder.tracks.LeftArm) {
      expect(kf.r[2]).toBeLessThan(-1.0)
    }

    for (const kf of placeholder.tracks.RightArm) {
      expect(kf.r[2]).toBeGreaterThan(1.0)
    }
  })
})
