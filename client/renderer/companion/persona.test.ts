import { describe, expect, it } from 'vitest'

import { assembleCharacterPersona, assemblePersona } from './persona'

describe('assemblePersona', () => {
  it('maps name/personality and preserves background and speaking_style', () => {
    const p = assemblePersona({ name: '小光', personality: '温柔体贴', role: '专属管家', speaking_style: '轻声细语' })
    expect(p).toEqual({ name: '小光', personality: '温柔体贴', speaking_style: '轻声细语', background: '专属管家' })
  })

  it('maps species / character_gender to biological_type / gender', () => {
    const p = assemblePersona({
      name: '梦鳞',
      species: '灵兽',
      character_gender: '女',
      appearance_core: '金发绿眼'
    })

    expect(p.biological_type).toBe('灵兽')
    expect(p.gender).toBe('女')
    expect(p.appearance_core).toBe('金发绿眼')
  })

  it('passes user_* fields through verbatim (backend routes them to memory)', () => {
    const p = assemblePersona({
      name: '梦鳞',
      personality: '温柔',
      user_call_name: '老板',
      user_hobbies: '音乐',
      user_freeform: '早起型'
    })

    expect(p.user_call_name).toBe('老板')
    expect(p.user_hobbies).toBe('音乐')
    expect(p.user_freeform).toBe('早起型')
  })

  it('drops empty user_* keys so PUT body stays clean', () => {
    const p = assemblePersona({ name: '小光', user_call_name: '', user_hobbies: '   ' })
    expect(p).not.toHaveProperty('user_call_name')
    expect(p).not.toHaveProperty('user_hobbies')
  })

  it('truncates appearance_core to 500 chars (matches schema max_length)', () => {
    const long = '金'.repeat(800)
    const p = assemblePersona({ name: '小光', appearance_core: long })
    expect(p.appearance_core?.length).toBe(500)
  })

  it('truncates appearance_outfit to 500 chars (matches schema max_length)', () => {
    const long = '音'.repeat(800)
    const p = assemblePersona({ name: '小光', appearance_outfit: long })
    expect(p.appearance_outfit?.length).toBe(500)
  })

  it('truncates user_* free-text fields to 2000 chars (matches schema max_length)', () => {
    const long = '音'.repeat(4000)
    const p = assemblePersona({ name: '小光', user_freeform: long, user_hobbies: long })
    expect(p.user_freeform?.length).toBe(2000)
    expect(p.user_hobbies?.length).toBe(2000)
  })

  it('defaults skipped required fields (name / personality) without injecting personality into speaking_style', () => {
    const p = assemblePersona({ name: '小光' })
    expect(p.name).toBe('小光')
    expect(p.personality).toBe('温柔体贴')
    expect(p.speaking_style).toBe('')
    expect(p.background).toBeUndefined()
  })

  it('preserves locked visual-anchor fields from previous when not in answers', () => {
    // 「锁定」特性允许锁定后的调用方（persona-editor / persona-retune）在 `answers` 里
    // 省略 species / character_gender / appearance_core。后端 PUT /persona 做的是整体替换，
    // 所以客户端必须把当前 persona 里的这些字段重新带过去，否则会被清空。
    const previous = {
      biological_type: '灵兽',
      gender: '女',
      appearance_core: '金发绿眼',
      background: '专属管家',
      appearance_outfit: '黑色礼帽'
    }

    const p = assemblePersona({ name: '小光', personality: '温柔', role: '管家' }, previous)
    expect(p.biological_type).toBe('灵兽')
    expect(p.gender).toBe('女')
    expect(p.appearance_core).toBe('金发绿眼')
    expect(p.background).toBe('管家')
  })

  it('uses new answer value when present, falling back to previous otherwise', () => {
    const previous = {
      biological_type: '灵兽',
      gender: '女',
      appearance_core: '金发绿眼',
      background: '专属管家',
      appearance_outfit: '黑色礼帽'
    }

    const p = assemblePersona({ name: '小光', appearance_outfit: '白色连衣裙' }, previous)
    expect(p.appearance_outfit).toBe('白色连衣裙')
    // appearance_core not in answers → preserved from previous
    expect(p.appearance_core).toBe('金发绿眼')
  })

  it('prefers user-picked speaking_style, falling back to previous otherwise', () => {
    const previous = { speaking_style: '上次选的说话风格' }
    expect(assemblePersona({ name: '小光', personality: '温柔' }, previous).speaking_style).toBe('上次选的说话风格')
    expect(assemblePersona({ name: '小光', personality: '温柔' }).speaking_style).toBe('')
    expect(
      assemblePersona({ name: '小光', personality: '温柔', speaking_style: '专业干练' }, previous).speaking_style
    ).toBe('专业干练')
  })

  it('regression: persona-editor save must not wipe locked fields (P0)', () => {
    // 模拟 persona-editor 的保存路径：调用方只知道 name / role / personality / appearance_outfit，
    // 但绝不能清掉 biological_type / gender / appearance_core。
    const previous = {
      biological_type: '灵兽',
      gender: '女',
      appearance_core: '金发绿眼'
    }

    const p = assemblePersona(
      { name: '小光', personality: '专业干练', role: '管家', appearance_outfit: '西装' },
      previous
    )

    expect(p.biological_type).toBe('灵兽')
    expect(p.gender).toBe('女')
    expect(p.appearance_core).toBe('金发绿眼')
    expect(p.name).toBe('小光')
    expect(p.personality).toBe('专业干练')
    expect(p.background).toBe('管家')
    expect(p.appearance_outfit).toBe('西装')
  })

  it('regression: persona-editor save preserves custom speaking_style via previous fallback', () => {
    // persona-editor save() 没把 speaking_style 放进 answers——
    // 保留用户挑选说话风格的，靠的就是 previous 回退链（`previous?.speaking_style?.trim()`）。
    const previous = { speaking_style: '上次选的说话风格' }
    const p = assemblePersona({ name: '小光', personality: '专业干练', role: '管家' }, previous)
    expect(p.speaking_style).toBe('上次选的说话风格')
  })

  it('regression: does not copy step 7 personality into step 8 speaking_style', () => {
    const p = assemblePersona({ name: '小艾', personality: '活泼开朗' })
    expect(p.personality).toBe('活泼开朗')
    expect(p.speaking_style).toBe('')
  })

  it('falls back to a default name when name is missing', () => {
    expect(assemblePersona({}).name).toBe('伙伴')
  })

  it('never leaks voice into the persona (TTS layer, not persona; user_* go to memory, not persona either)', () => {
    const p = assemblePersona({ name: '小光', voice: '少女音' })
    expect(p).not.toHaveProperty('voice')
    expect(Object.keys(p).sort()).toEqual(['name', 'personality', 'speaking_style'])
  })

  it('uses user-picked speaking_style directly without heuristic modification', () => {
    const p = assemblePersona({
      name: '小光',
      personality: '毒舌傲娇',
      speaking_style: '专业干练'
    })

    expect(p.speaking_style).toBe('专业干练')
  })

  it('keeps appearance_core and appearance_outfit as independent fields', () => {
    const p = assemblePersona({
      name: '小光',
      appearance_core: '金发绿眼',
      appearance_outfit: '黑色礼帽'
    })

    expect(p.appearance_core).toBe('金发绿眼')
    expect(p.appearance_outfit).toBe('黑色礼帽')
  })
})

describe('assembleCharacterPersona', () => {
  it('strips every user_* key so PUT never carries empty user fields', () => {
    const p = assembleCharacterPersona({
      name: '小光',
      personality: '温柔',
      user_call_name: '老板',
      user_hobbies: '摄影'
    })

    expect(p).not.toHaveProperty('user_call_name')
    expect(p).not.toHaveProperty('user_hobbies')
    expect(p).not.toHaveProperty('user_gender')
    expect(p).not.toHaveProperty('user_age_bucket')
    expect(p).not.toHaveProperty('user_freeform')
  })

  it('preserves character fields verbatim (species/role/appearance_core/background)', () => {
    const p = assembleCharacterPersona({
      name: '梦鳞',
      species: '灵兽',
      character_gender: '女',
      appearance_core: '金发',
      role: '专属管家',
      personality: '温柔体贴',
      speaking_style: '利落'
    })

    expect(p.name).toBe('梦鳞')
    expect(p.biological_type).toBe('灵兽')
    expect(p.gender).toBe('女')
    expect(p.appearance_core).toBe('金发')
    expect(p.background).toBe('专属管家')
    expect(p.personality).toBe('温柔体贴')
    expect(p.speaking_style).toBe('利落')
  })

  it('never leaks voice (TTS field, not a persona field)', () => {
    const p = assembleCharacterPersona({ name: '小光', voice: '少女音' })
    expect(p).not.toHaveProperty('voice')
  })

  it('matches a full character + user answers payload modulo user_* keys', () => {
    const full = assemblePersona({
      name: '小光',
      personality: '温柔',
      speaking_style: '俏皮',
      species: '灵兽',
      role: '专属管家',
      user_call_name: '老板',
      user_hobbies: '摄影'
    })

    const characterOnly = assembleCharacterPersona({
      name: '小光',
      personality: '温柔',
      speaking_style: '俏皮',
      species: '灵兽',
      role: '专属管家',
      user_call_name: '老板',
      user_hobbies: '摄影'
    })

    for (const key of Object.keys(characterOnly) as Array<keyof typeof characterOnly>) {
      expect(full).toHaveProperty(key)
      expect(full[key]).toEqual(characterOnly[key])
    }

    expect(characterOnly).not.toHaveProperty('user_call_name')
    expect(characterOnly).not.toHaveProperty('user_hobbies')
  })
})
