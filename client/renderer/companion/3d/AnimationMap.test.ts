import { describe, expect, it } from 'vitest'

import { resolveClip } from './AnimationMap'

const BIPED_MAP = {
  idle: 'preset:biped:idle',
  emotional: 'preset:biped:laugh_01',
  poke: 'preset:biped:jump',
  walk: 'preset:biped:walk'
}

// 非 biped 每类只有一个预设，全部语义键指向它。
const QUADRUPED_MAP = {
  idle: 'preset:quadruped:walk',
  emotional: 'preset:quadruped:walk',
  poke: 'preset:quadruped:walk'
}

describe('resolveClip', () => {
  it('全等命中供应商原样写入的 clip 名', () => {
    expect(resolveClip('idle', BIPED_MAP, new Set(['preset:biped:idle']))).toBe('preset:biped:idle')
  })

  it('供应商只写叶名时按叶名全等命中', () => {
    expect(resolveClip('walk', BIPED_MAP, new Set(['walk', 'idle']))).toBe('walk')
  })

  it('带前缀且大小写不同的实际命名仍能子串命中', () => {
    expect(resolveClip('walk', BIPED_MAP, new Set(['Armature|Walk']))).toBe('Armature|Walk')
  })

  it('叶名全等优先于子串命中', () => {
    expect(resolveClip('walk', BIPED_MAP, new Set(['sidewalk_loop', 'walk']))).toBe('walk')
  })

  it('单 clip 产物下所有语义键收敛到同一个动作', () => {
    const available = new Set(['preset:quadruped:walk'])
    const resolved = ['idle', 'emotional', 'poke'].map(k => resolveClip(k, QUADRUPED_MAP, available))

    expect(new Set(resolved)).toEqual(new Set(['preset:quadruped:walk']))
  })

  it('映射缺键时退到 idle —— idle 是产品级语义键，不是供应商命名', () => {
    expect(resolveClip('listening', BIPED_MAP, new Set(['preset:biped:idle']))).toBe('preset:biped:idle')
  })

  it('空映射返回 null（avian 与存量老模型：无动画，停在绑定姿势）', () => {
    expect(resolveClip('idle', {}, new Set(['preset:biped:idle']))).toBeNull()
  })

  it('映射有键但 GLB 里没有对应 clip 时返回 null', () => {
    expect(resolveClip('walk', BIPED_MAP, new Set(['preset:biped:idle']))).toBeNull()
  })

  it('GLB 完全没有内嵌 clip 时返回 null', () => {
    expect(resolveClip('idle', BIPED_MAP, new Set())).toBeNull()
  })
})
