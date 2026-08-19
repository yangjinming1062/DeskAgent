import { cleanup, render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { isPointInteractive } from '@/companion/interactive-regions'
import { SpriteStage } from '@/companion/sprite/sprite-stage'

describe('SpriteStage', () => {
  beforeEach(() => {
    // mock window.spiritagent.sprite 方法
    window.spiritagent = {
      ...window.spiritagent,
      sprite: {
        setIgnoreMouseEvents: vi.fn().mockResolvedValue(undefined),
        moveToCursorDisplay: vi.fn().mockResolvedValue(null),
        getPosition: vi.fn().mockResolvedValue({ x: 100, y: 100 }),
        setPosition: vi.fn().mockResolvedValue(undefined),
        setAlwaysOnTop: vi.fn().mockResolvedValue(undefined)
      }
    } as unknown as typeof window.spiritagent
  })

  afterEach(() => {
    cleanup()
  })

  it('renders children and registers interactive region when visible', () => {
    const { container } = render(
      <SpriteStage hidden={false}>
        <div data-testid="child">3D Companion</div>
      </SpriteStage>
    )

    const stageEl = container.querySelector('.absolute') as HTMLElement
    expect(stageEl).toBeDefined()
    expect(stageEl.style.visibility).toBe('visible')
    expect(stageEl.style.opacity).toBe('1')
  })

  it('hides container and yields no interactive region when hidden=true', () => {
    const { container } = render(
      <SpriteStage hidden={true}>
        <div data-testid="child">3D Companion</div>
      </SpriteStage>
    )

    const stageEl = container.querySelector('.absolute') as HTMLElement
    expect(stageEl).toBeDefined()
    expect(stageEl.style.visibility).toBe('hidden')
    expect(stageEl.style.opacity).toBe('0')
    expect(stageEl.style.pointerEvents).toBe('none')

    // 隐藏时区域命中检测返回 false
    expect(isPointInteractive(100, 100)).toBe(false)
  })
})
