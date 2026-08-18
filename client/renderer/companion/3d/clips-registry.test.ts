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
  it('lists all 7 rig types', () => {
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
  ])('provides the canonical state clips for %s', (rig, defs) => {
    // AnimationMap.resolveClip looks up states by these exact names; a rig
    // library missing one leaves the character unable to animate that state.
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

  it('falls back to biped for unknown rig types', () => {
    expect(getClipDefs('avian-but-spelled-wrong')).toBe(BIPED_CLIPS)
    expect(getClipDefs(null)).toBe(BIPED_CLIPS)
    expect(getClipDefs('')).toBe(BIPED_CLIPS)
  })

  it('falls back to biped for unsupported rig types', () => {
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
  ])('builds THREE.AnimationClip instances for %s', (rig, expectedCount) => {
    const clips = buildClipsForRig(rig)
    expect(clips.length).toBe(expectedCount)

    for (const clip of clips) {
      expect(clip.name).toBeTruthy()
      expect(clip.duration).toBeGreaterThan(0)
    }
  })

  it('summarizes every rig library with exact counts', () => {
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

  it('exposes clip names per rig type', () => {
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
  ])('ensures clips in %s have valid definitions and tags', (rig, defs) => {
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

  it('ensures biped idle clip provides natural standing posture rather than stiff A-pose', () => {
    const idle = BIPED_CLIPS.idle
    expect(idle).toBeDefined()
    expect(idle.tracks).toHaveProperty('LeftArm')
    expect(idle.tracks).toHaveProperty('RightArm')
    expect(idle.tracks).toHaveProperty('LeftForeArm')
    expect(idle.tracks).toHaveProperty('RightForeArm')
    expect(idle.tracks).toHaveProperty('Spine')
    expect(idle.tracks).toHaveProperty('Head')

    // Natural arm drop should rotate arm down towards torso (LeftArm Z < -1.0 rad (~-57°+), RightArm Z > 1.0 rad (~+57°+))
    for (const kf of idle.tracks.LeftArm) {
      expect(kf.r[2]).toBeLessThan(-1.0)
    }

    for (const kf of idle.tracks.RightArm) {
      expect(kf.r[2]).toBeGreaterThan(1.0)
    }

    // Forearms should have natural elbow flexion (> 0.1 rad)
    for (const kf of idle.tracks.LeftForeArm) {
      expect(kf.r[0]).toBeGreaterThan(0.1)
    }

    for (const kf of idle.tracks.RightForeArm) {
      expect(kf.r[0]).toBeGreaterThan(0.1)
    }
  })

  it('ensures placeholder biped clips include natural resting arm tracks to prevent A-pose pop', () => {
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
