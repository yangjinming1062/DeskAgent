import { beforeEach, describe, expect, it, vi } from 'vitest'

const hoisted = vi.hoisted(() => ({
  events: [] as string[],
  resumeGate: undefined as (() => Promise<void>) | undefined
}))

class FakeAudio {
  static instances: FakeAudio[] = []

  src: string | null
  private listeners = new Map<string, Set<EventListener>>()

  constructor(src: string) {
    this.src = src
    FakeAudio.instances.push(this)
  }

  addEventListener(type: string, listener: EventListener): void {
    const set = this.listeners.get(type) ?? new Set<EventListener>()
    set.add(listener)
    this.listeners.set(type, set)
  }

  removeEventListener(type: string, listener: EventListener): void {
    this.listeners.get(type)?.delete(listener)
  }

  removeAttribute(name: 'src'): void {
    if (name === 'src') {
      this.src = null
    }
  }

  load(): void {}

  pause(): void {}

  async play(): Promise<void> {
    hoisted.events.push('play')
  }

  emit(type: 'ended' | 'error'): void {
    for (const listener of this.listeners.get(type) ?? []) {
      listener(new Event(type))
    }
  }
}

vi.mock('@/shared/lib/audio-context-ctor', () => ({
  getAudioContextCtor: () =>
    class FakeAudioContext {
      state: AudioContextState = 'suspended'
      destination = {}

      async resume(): Promise<void> {
        hoisted.events.push('resume')
        await hoisted.resumeGate?.()
        this.state = 'running'
      }

      createAnalyser() {
        return {
          fftSize: 0,
          frequencyBinCount: 1024,
          connect: (node: unknown) => hoisted.events.push('analyser.connect', String(node === this.destination)),
          getByteTimeDomainData: (buffer: Uint8Array) => buffer.fill(128)
        }
      }

      createMediaElementSource(audio: FakeAudio) {
        hoisted.events.push(`source:${audio.src}`)

        return { connect: () => hoisted.events.push('source.connect') }
      }
    }
}))

beforeEach(() => {
  vi.resetModules()
  vi.stubGlobal('Audio', FakeAudio)
  vi.stubGlobal('requestAnimationFrame', () => {
    hoisted.events.push('requestAnimationFrame')

    return 1
  })
  vi.stubGlobal('cancelAnimationFrame', () => {})
  FakeAudio.instances = []
  hoisted.events = []
  hoisted.resumeGate = undefined
})

describe('playDataUrl', () => {
  it('waits for the suspended AudioContext to resume before starting playback', async () => {
    let resolveResume: (() => void) | undefined
    hoisted.resumeGate = () => new Promise<void>(resolve => (resolveResume = resolve))

    const { playDataUrl } = await import('./audio-track')
    const playback = playDataUrl('data:audio/mpeg;base64,α')

    await vi.waitFor(() => expect(hoisted.events).toContain('resume'))
    await Promise.resolve()
    await Promise.resolve()
    expect(hoisted.events).not.toContain('play')

    resolveResume?.()
    await vi.waitFor(() => expect(hoisted.events).toContain('play'))

    expect(hoisted.events.indexOf('resume')).toBeLessThan(hoisted.events.indexOf('play'))
    expect(hoisted.events.indexOf('source.connect')).toBeLessThan(hoisted.events.indexOf('play'))

    FakeAudio.instances[0].emit('ended')
    await expect(playback).resolves.toBe(true)
  })

  it('plays directly when the AudioContext cannot resume', async () => {
    hoisted.resumeGate = () => Promise.reject(new Error('not allowed'))

    const { playDataUrl } = await import('./audio-track')
    const playback = playDataUrl('data:audio/mpeg;base64,β')

    await vi.waitFor(() => expect(hoisted.events).toContain('play'))
    expect(hoisted.events).not.toContainEqual('source:data:audio/mpeg;base64,β')

    FakeAudio.instances[0].emit('ended')
    await expect(playback).resolves.toBe(true)
  })
})
