import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { SubagentProgress } from '@/store/subagents'

import { SubagentsPopover } from './subagents-popover'

const baseItem: SubagentProgress = {
  id: 'a1',
  parentId: null,
  goal: 'scan files',
  status: 'running',
  taskCount: 1,
  taskIndex: 0,
  startedAt: 0,
  updatedAt: 0,
  filesRead: [],
  filesWritten: [],
  stream: []
}

describe('SubagentsPopover', () => {
  it('renders an empty state when no subagents exist', () => {
    render(<SubagentsPopover items={[]} onOpen={() => {}} />)

    expect(screen.getByText('No live subagents')).toBeDefined()
  })

  it('renders a clickable row for a subagent that has a session_id', () => {
    const onOpen = vi.fn()
    render(
      <SubagentsPopover items={[{ ...baseItem, id: 'real', sessionId: '42', goal: 'find docs' }]} onOpen={onOpen} />
    )

    const button = screen.getByRole('button', { name: /find docs/i })

    expect(button.hasAttribute('disabled')).toBe(false)

    fireEvent.click(button)

    expect(onOpen).toHaveBeenCalledWith('42')
  })

  it('renders a disabled row when a subagent has no session_id', () => {
    render(
      <SubagentsPopover
        items={[{ ...baseItem, id: 'delegate-fallback', status: 'running', goal: 'fallback path' }]}
        onOpen={() => {}}
      />
    )

    const button = screen.getByRole('button', { name: /fallback path/i })

    expect(button.hasAttribute('disabled')).toBe(true)
  })
})
