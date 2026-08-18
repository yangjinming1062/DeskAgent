import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ChatDock } from '@/companion/chat-dock'
import { $chatMessages } from '@/companion/chat-store'
import { $spriteEmotion, $spriteState } from '@/companion/companion-store'
import { $portraitUrl } from '@/companion/portrait-store'
import { $gatewayState } from '@/shared/store/gateway'

vi.mock('@/companion/boot/use-gateway-request', () => ({
  useGatewayRequest: () => ({
    requestGateway: vi.fn().mockResolvedValue({})
  })
}))

describe('ChatDock', () => {
  beforeEach(() => {
    $chatMessages.set([])
    $gatewayState.set('open')
    $spriteState.set('idle')
    $spriteEmotion.set('happy')
    $portraitUrl.set('http://test/avatar.png')

    window.spiritagent = {
      ...window.spiritagent,
      api: vi.fn().mockResolvedValue({}),
      saveClipboardImage: vi.fn(),
      readFileDataUrl: vi.fn()
    } as unknown as typeof window.spiritagent
  })

  afterEach(() => {
    cleanup()
  })

  it('renders two-column layout with left emotion state and right chat panel', () => {
    const onClose = vi.fn()
    const onOpenVoiceCall = vi.fn()
    render(<ChatDock onClose={onClose} onOpenVoiceCall={onOpenVoiceCall} />)

    // Left visual anchor elements: emotion state and status
    expect(screen.getByText('开心愉悦')).toBeDefined()
    expect(screen.getByText('当前情绪状态')).toBeDefined()
    expect(screen.getByText('在线陪伴')).toBeDefined()

    const avatarImg = screen.getByAltText('角色形象') as HTMLImageElement
    expect(avatarImg).toBeDefined()
    expect(avatarImg.src).toContain('avatar.png')

    // Right chat panel elements
    expect(screen.getByPlaceholderText('输入消息，Enter 发送，Shift+Enter 换行')).toBeDefined()
    expect(screen.getByRole('button', { name: '关闭对话' })).toBeDefined()

    // Voice call button in bottom bar
    const voiceCallBtn = screen.getByRole('button', { name: '📞 通话' })
    expect(voiceCallBtn).toBeDefined()
    fireEvent.click(voiceCallBtn)
    expect(onOpenVoiceCall).toHaveBeenCalledTimes(1)
  })

  it('reflects dynamic emotion changes in visual anchor', () => {
    $spriteEmotion.set('curious')
    render(<ChatDock onClose={vi.fn()} />)

    expect(screen.getByText('充满好奇')).toBeDefined()
  })

  it('reflects thinking state in visual anchor status badge', () => {
    $spriteState.set('thinking')
    render(<ChatDock onClose={vi.fn()} />)

    expect(screen.getByText('思考中…')).toBeDefined()
  })

  it('calls onClose when close button is clicked', () => {
    const onClose = vi.fn()
    render(<ChatDock onClose={onClose} />)

    const closeBtn = screen.getByRole('button', { name: '关闭对话' })
    fireEvent.click(closeBtn)

    expect(onClose).toHaveBeenCalledTimes(1)
  })
})
