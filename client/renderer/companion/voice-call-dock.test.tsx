import { cleanup, render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { getAudioExtensionForMime, getSupportedOpusMimeType, VoiceCallDock } from './voice-call-dock'

vi.mock('@/companion/boot/use-gateway-request', () => ({
  useGatewayRequest: () => ({
    requestGateway: vi.fn().mockResolvedValue({})
  })
}))

describe('VoiceCallDock audio constraints and format selection', () => {
  beforeEach(() => {
    window.spiritagent = {
      ...window.spiritagent,
      media: {
        stt: vi.fn().mockResolvedValue({ text: 'test' })
      },
      sprite: {
        setAlwaysOnTop: vi.fn().mockResolvedValue(undefined)
      }
    } as unknown as typeof window.spiritagent
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it('selects the first supported Opus MIME type based on MediaRecorder.isTypeSupported', () => {
    const isTypeSupported = vi.fn((type: string) => type === 'audio/ogg;codecs=opus')
    vi.stubGlobal('MediaRecorder', {
      isTypeSupported
    })

    expect(getSupportedOpusMimeType()).toBe('audio/ogg;codecs=opus')
    expect(isTypeSupported).toHaveBeenCalled()
  })

  it('falls back to undefined when no candidate Opus MIME is supported', () => {
    const isTypeSupported = vi.fn(() => false)
    vi.stubGlobal('MediaRecorder', {
      isTypeSupported
    })

    expect(getSupportedOpusMimeType()).toBeUndefined()
  })

  it('falls back to undefined when MediaRecorder API is missing', () => {
    vi.stubGlobal('MediaRecorder', undefined)
    expect(getSupportedOpusMimeType()).toBeUndefined()
  })

  it('maps MIME types to expected file extensions for STT upload', () => {
    expect(getAudioExtensionForMime('audio/webm;codecs=opus')).toBe('webm')
    expect(getAudioExtensionForMime('audio/webm')).toBe('webm')
    expect(getAudioExtensionForMime('audio/ogg;codecs=opus')).toBe('ogg')
    expect(getAudioExtensionForMime('audio/ogg')).toBe('ogg')
    expect(getAudioExtensionForMime('audio/mp4;codecs=opus')).toBe('mp4')
    expect(getAudioExtensionForMime('audio/mp4')).toBe('mp4')
  })

  it('requests getUserMedia with VOICE_CALL_AUDIO_CONSTRAINTS when mounted', async () => {
    const getUserMediaMock = vi.fn().mockResolvedValue({
      getTracks: () => [{ stop: vi.fn() }]
    })

    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia: getUserMediaMock },
      writable: true
    })

    render(<VoiceCallDock onClose={vi.fn()} />)

    expect(getUserMediaMock).toHaveBeenCalledWith({
      audio: {
        autoGainControl: true,
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        sampleRate: 16000
      }
    })
  })
})
