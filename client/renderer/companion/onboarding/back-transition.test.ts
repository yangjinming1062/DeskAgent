import { describe, expect, it } from 'vitest'

import { computeBackTransition } from './back-transition'

const CHARACTER_COUNT = 5

describe('computeBackTransition', () => {
  it('imageSealed 后所有返回路径都返回 null,不会重新进入 portrait-avatar / fullbody-3d', () => {
    // 三个会回到 q-character 或更早的入口,在 imageSealed 时都必须短路
    expect(
      computeBackTransition(
        { phase: 'q-character', qIndex: 3, voiceStage: 'describe', imageSealed: true },
        CHARACTER_COUNT
      )
    ).toBeNull()
    expect(
      computeBackTransition({ phase: 'voice', qIndex: 0, voiceStage: 'describe', imageSealed: true }, CHARACTER_COUNT)
    ).toBeNull()
    expect(
      computeBackTransition({ phase: 'q-user', qIndex: 0, voiceStage: 'catalog', imageSealed: true }, CHARACTER_COUNT)
    ).toBeNull()
  })

  it('q-character 中段返回到上一题', () => {
    expect(
      computeBackTransition(
        { phase: 'q-character', qIndex: 3, voiceStage: 'describe', imageSealed: false },
        CHARACTER_COUNT
      )
    ).toEqual({ phase: 'q-character', qIndex: 2 })
  })

  it('q-character 第一题已是边界,返回无变化', () => {
    expect(
      computeBackTransition(
        { phase: 'q-character', qIndex: 0, voiceStage: 'describe', imageSealed: false },
        CHARACTER_COUNT
      )
    ).toBeNull()
  })

  it('voice 描述阶段返回到 q-character 最后一题', () => {
    expect(
      computeBackTransition({ phase: 'voice', qIndex: 0, voiceStage: 'describe', imageSealed: false }, CHARACTER_COUNT)
    ).toEqual({ phase: 'q-character', qIndex: CHARACTER_COUNT - 1 })
  })

  it('voice 目录阶段不返回(避免回到 q-character 触发新的 hatching)', () => {
    expect(
      computeBackTransition({ phase: 'voice', qIndex: 0, voiceStage: 'catalog', imageSealed: false }, CHARACTER_COUNT)
    ).toBeNull()
  })

  it('q-user 中段返回到上一题', () => {
    expect(
      computeBackTransition({ phase: 'q-user', qIndex: 2, voiceStage: 'describe', imageSealed: false }, CHARACTER_COUNT)
    ).toEqual({ phase: 'q-user', qIndex: 1 })
  })

  it('q-user 第一题返回到 voice 目录阶段(单向回退,不再回到 q-character)', () => {
    expect(
      computeBackTransition({ phase: 'q-user', qIndex: 0, voiceStage: 'describe', imageSealed: false }, CHARACTER_COUNT)
    ).toEqual({ phase: 'voice', voiceStage: 'catalog' })
  })
})
