import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'

import { pushUserMessage } from './chat-store'
import { SubtitlesOverlay } from './subtitles-overlay'

describe('SubtitlesOverlay component', () => {
  beforeEach(() => {
    pushUserMessage('测试字幕消息')
  })

  it('renders subtitles when visible', () => {
    render(<SubtitlesOverlay visible={true} />)
    expect(screen.getByText('测试字幕消息')).toBeTruthy()
  })

  it('does not render subtitles when visible is false', () => {
    const { container } = render(<SubtitlesOverlay visible={false} />)
    expect(container.firstChild).toBeNull()
  })
})
