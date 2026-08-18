import { renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'

import { usePanelResize } from './use-panel-resize'

describe('usePanelResize', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('initializes with default size when storage is empty', () => {
    const { result } = renderHook(() =>
      usePanelResize({
        sizeStorageKey: 'test.size',
        offsetStorageKey: 'test.offset',
        defaultSize: { width: 760, height: 540 },
        getPanel: () => null
      })
    )

    expect(result.current.size).toEqual({ width: 760, height: 540 })
  })

  it('restores stored size from localStorage within bounds', () => {
    localStorage.setItem('test.size', JSON.stringify({ width: 850, height: 600 }))

    const { result } = renderHook(() =>
      usePanelResize({
        sizeStorageKey: 'test.size',
        offsetStorageKey: 'test.offset',
        defaultSize: { width: 760, height: 540 },
        minSize: { width: 560, height: 400 },
        maxSize: { width: 1400, height: 900 },
        getPanel: () => null
      })
    )

    expect(result.current.size).toEqual({ width: 850, height: 600 })
  })

  it('provides resize handle props for 8 directions', () => {
    const { result } = renderHook(() =>
      usePanelResize({
        sizeStorageKey: 'test.size',
        offsetStorageKey: 'test.offset',
        defaultSize: { width: 760, height: 540 },
        getPanel: () => null
      })
    )

    const directions = ['n', 's', 'e', 'w', 'ne', 'nw', 'se', 'sw'] as const

    for (const dir of directions) {
      const props = result.current.getResizeHandleProps(dir)
      expect(props.onPointerDown).toBeTypeOf('function')
      expect(props.onPointerMove).toBeTypeOf('function')
      expect(props.onPointerUp).toBeTypeOf('function')
    }
  })
})
