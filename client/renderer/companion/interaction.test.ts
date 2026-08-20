import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { $availableClipNames, $clipMap } from './3d/model-store'
import { $clipOverride, $effectiveTierOverride, $spriteState, $userPreferredTier } from './companion-store'
import { handleDragEndInteraction, handlePokeInteraction } from './interaction'

const hoisted = vi.hoisted(() => {
  return {
    playReactionAudio: vi.fn(),
    reportInteractionStat: vi.fn(),
    gatewayRequest: vi.fn()
  }
})

vi.mock('@/shared/store/gateway', () => ({
  $gateway: {
    get: () => ({
      request: hoisted.gatewayRequest
    })
  }
}))

// 把请求的 bucket 透传回入口，让下方的分发断言能区分 poke-light 与 drag。
vi.mock('./reactions/reaction-audio', () => ({
  pickReaction: (bucket: string) => ({
    id: `reaction.${bucket}.gentle.0`,
    tags: ['温柔'],
    bucket,
    text: '嗯？怎么啦？'
  }),
  playReactionAudio: hoisted.playReactionAudio
}))

vi.mock('./activity', () => ({
  reportInteractionStat: hoisted.reportInteractionStat
}))

vi.mock('./persona-store', () => ({
  $personalityTags: {
    get: () => ['温柔']
  }
}))

beforeEach(() => {
  vi.useFakeTimers()
  // 清理之前测试残留的 setTimeout——interaction.ts 使用
  // 一个模块级的重置计时器，每次戳后 4 秒触发。不清理的话，
  // 上一个测试的回调可能在套件中途重置 pokeCount。
  vi.clearAllTimers()
  vi.setSystemTime(new Date(10_000))
  hoisted.playReactionAudio.mockClear()
  hoisted.reportInteractionStat.mockClear()
  hoisted.gatewayRequest.mockClear()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('poke / drag dispatch into reaction audio', () => {
  it('handlePokeInteraction fires interacting state + playReactionAudio with bucket=poke-light', () => {
    handlePokeInteraction()

    expect($spriteState.get()).toBe('interacting')
    expect(hoisted.playReactionAudio).toHaveBeenCalledTimes(1)
    expect(hoisted.playReactionAudio.mock.calls[0][0]).toMatchObject({ bucket: 'poke-light' })
  })

  it('escalates the poke bucket across a tight burst (light → medium → heavy)', async () => {
    // 全新模块——pokeCount 是模块状态，前面测试已经推进过它。
    vi.resetModules()
    const { handlePokeInteraction: poke } = await import('./interaction')

    poke()
    poke()
    expect(hoisted.playReactionAudio.mock.calls[1][0]).toMatchObject({ bucket: 'poke-light' })

    poke()
    expect(hoisted.playReactionAudio.mock.calls[2][0]).toMatchObject({ bucket: 'poke-medium' })

    poke()
    poke()
    expect(hoisted.playReactionAudio.mock.calls[4][0]).toMatchObject({ bucket: 'poke-heavy' })
  })

  it('reports a poke stat fire-and-forget on handlePokeInteraction', () => {
    handlePokeInteraction()

    expect(hoisted.reportInteractionStat).toHaveBeenCalledWith('poke')
  })

  it('handleDragEndInteraction plays local drag reaction and never issues RPC or reports stat', () => {
    handleDragEndInteraction()

    expect(hoisted.playReactionAudio).toHaveBeenCalledTimes(1)
    expect(hoisted.playReactionAudio.mock.calls[0][0]).toMatchObject({ bucket: 'drag' })
    expect(hoisted.reportInteractionStat).not.toHaveBeenCalled()
    expect(hoisted.gatewayRequest).not.toHaveBeenCalled()
  })

  it('handles empty manifest by passing null through playReactionAudio', () => {
    vi.resetModules()
    vi.doMock('./reactions/reaction-audio', () => ({
      pickReaction: () => null,
      playReactionAudio: hoisted.playReactionAudio
    }))

    return import('./interaction').then(mod => {
      mod.handlePokeInteraction()
      expect(hoisted.playReactionAudio).toHaveBeenCalledTimes(1)
      expect(hoisted.playReactionAudio.mock.calls[0][0]).toBeNull()
    })
  })

  it('quiet tier does not affect reaction playback (handled by audio-track)', () => {
    $effectiveTierOverride.set('quiet')
    $userPreferredTier.set('quiet')
    handlePokeInteraction()

    expect(hoisted.playReactionAudio).toHaveBeenCalledTimes(1)
    $effectiveTierOverride.set(null)
    $userPreferredTier.set('normal')
  })
})

describe('poke 兑现到 GLB 内嵌 clip', () => {
  it('把语义键 poke 兑现进 $clipOverride，而非一律落到通用 interacting 动作', () => {
    $clipMap.set({ idle: 'preset:biped:idle', poke: 'preset:biped:jump' })
    $availableClipNames.set(new Set(['preset:biped:idle', 'preset:biped:jump']))
    $clipOverride.set(null)

    handlePokeInteraction()

    expect($clipOverride.get()).toBe('preset:biped:jump')
  })

  it('映射为空（avian / 存量老模型）时不设置覆盖，角色停在绑定姿势', () => {
    $clipMap.set({})
    $availableClipNames.set(new Set())
    $clipOverride.set(null)

    handlePokeInteraction()

    expect($clipOverride.get()).toBeNull()
  })
})
