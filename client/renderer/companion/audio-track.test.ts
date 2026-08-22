import { beforeEach, describe, expect, it, vi } from 'vitest'

const hoisted = vi.hoisted(() => ({
  events: [] as string[],
  resumeGate: undefined as (() => Promise<void>) | undefined
}))

let nextRafId = 1
const rafCallbacks = new Map<number, FrameRequestCallback>()

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
    hoisted.events.push(`play:${this.src}`)
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

      async suspend(): Promise<void> {
        hoisted.events.push('suspend')
        this.state = 'suspended'
      }

      createAnalyser() {
        return {
          fftSize: 0,
          frequencyBinCount: 1024,
          connect: (node: unknown) => hoisted.events.push('analyser.connect', String(node === this.destination)),
          getByteTimeDomainData: (buffer: Uint8Array) => buffer.fill(160)
        }
      }

      createMediaElementSource(audio: FakeAudio) {
        hoisted.events.push(`source:${audio.src}`)

        return {
          connect: () => hoisted.events.push('source.connect'),
          disconnect: () => hoisted.events.push(`disconnect:${audio.src}`)
        }
      }
    }
}))

beforeEach(() => {
  vi.resetModules()
  vi.stubGlobal('Audio', FakeAudio)
  nextRafId = 1
  rafCallbacks.clear()
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => {
    const id = nextRafId++
    rafCallbacks.set(id, cb)
    hoisted.events.push(`requestAnimationFrame:${id}`)

    return id
  })
  vi.stubGlobal('cancelAnimationFrame', (id: number) => {
    rafCallbacks.delete(id)
    hoisted.events.push(`cancelAnimationFrame:${id}`)
  })
  FakeAudio.instances = []
  hoisted.events = []
  hoisted.resumeGate = undefined
})

describe('playDataUrl', () => {
  it('starts playback immediately without waiting for AudioContext resume', async () => {
    let resolveResume: (() => void) | undefined
    hoisted.resumeGate = () => new Promise<void>(resolve => (resolveResume = resolve))

    const { playDataUrl } = await import('./audio-track')
    const playback = playDataUrl('data:audio/mpeg;base64,α')

    await vi.waitFor(() => expect(hoisted.events).toContain('play:data:audio/mpeg;base64,α'))
    await vi.waitFor(() => expect(hoisted.events).toContain('resume'))

    resolveResume?.()

    FakeAudio.instances[0].emit('ended')
    await expect(playback).resolves.toBe(true)
  })

  it('plays directly when the AudioContext cannot resume', async () => {
    hoisted.resumeGate = () => Promise.reject(new Error('not allowed'))

    const { playDataUrl } = await import('./audio-track')
    const playback = playDataUrl('data:audio/mpeg;base64,β')

    await vi.waitFor(() => expect(hoisted.events).toContain('play:data:audio/mpeg;base64,β'))
    // resume 被拒时 analyser 管线不会接上——这是预期行为。音频本身仍然能播放。
    expect(hoisted.events).not.toContainEqual('source:data:audio/mpeg;base64,β')

    FakeAudio.instances[0].emit('ended')
    await expect(playback).resolves.toBe(true)
  })

  it('prevents stale playback from overriding analyser when fast interrupted by new playback', async () => {
    let resolveResume1: (() => void) | undefined
    let resolveResume2: (() => void) | undefined

    let callCount = 0

    hoisted.resumeGate = () => {
      callCount++

      if (callCount === 1) {
        return new Promise<void>(resolve => (resolveResume1 = resolve))
      }

      return new Promise<void>(resolve => (resolveResume2 = resolve))
    }

    const { playDataUrl } = await import('./audio-track')

    const playback1 = playDataUrl('data:audio/mpeg;base64,audio-1')
    await vi.waitFor(() => expect(hoisted.events).toContain('play:data:audio/mpeg;base64,audio-1'))

    const playback2 = playDataUrl('data:audio/mpeg;base64,audio-2')
    await vi.waitFor(() => expect(hoisted.events).toContain('play:data:audio/mpeg;base64,audio-2'))

    FakeAudio.instances[0].emit('ended')
    await expect(playback1).resolves.toBe(false)

    resolveResume1?.()

    expect(hoisted.events).not.toContain('source:data:audio/mpeg;base64,audio-1')

    resolveResume2?.()

    await vi.waitFor(() => expect(hoisted.events).toContain('source:data:audio/mpeg;base64,audio-2'))

    FakeAudio.instances[1].emit('ended')
    await expect(playback2).resolves.toBe(true)
  })

  it('stops rAF loop and prevents late resume from starting rAF after stopAudio', async () => {
    let resolveResume: (() => void) | undefined
    hoisted.resumeGate = () => new Promise<void>(resolve => (resolveResume = resolve))

    const { playDataUrl, stopAudio } = await import('./audio-track')
    const playback = playDataUrl('data:audio/mpeg;base64,cancelled')

    await vi.waitFor(() => expect(hoisted.events).toContain('play:data:audio/mpeg;base64,cancelled'))

    stopAudio()
    await expect(playback).resolves.toBe(false)

    const rAfCountBefore = hoisted.events.filter(e => e.startsWith('requestAnimationFrame')).length

    resolveResume?.()

    await new Promise(r => setTimeout(r, 20))

    expect(hoisted.events).not.toContain('source:data:audio/mpeg;base64,cancelled')
    const rAfCountAfter = hoisted.events.filter(e => e.startsWith('requestAnimationFrame')).length
    expect(rAfCountAfter).toBe(rAfCountBefore)
  })

  it('registers amplitude sink and feeds amplitude values during playback and resets to 0 on stop', async () => {
    const { playDataUrl, registerAmplitudeSink, stopAudio } = await import('./audio-track')

    const amplitudes: number[] = []
    const unregister = registerAmplitudeSink(amp => amplitudes.push(amp))

    const playback = playDataUrl('data:audio/mpeg;base64,amp-test')
    await vi.waitFor(() => expect(hoisted.events).toContain('source:data:audio/mpeg;base64,amp-test'))

    // 160 (mock fill) - 128 = 32 abs dev per sample; avg = 32; amp = min(1, 32 / 96) ≈ 0.333
    expect(rafCallbacks.size).toBeGreaterThan(0)
    const activeCb = [...rafCallbacks.values()][0]
    activeCb(100)

    expect(amplitudes.length).toBeGreaterThan(0)
    expect(amplitudes[amplitudes.length - 1]).toBeCloseTo(0.333, 2)

    stopAudio()
    expect(amplitudes[amplitudes.length - 1]).toBe(0)

    unregister()
    FakeAudio.instances[0].emit('ended')
    await expect(playback).resolves.toBe(false)
  })

  it('cancels amplitude rAF loop and clears analyser on natural playback ended', async () => {
    const { playDataUrl, registerAmplitudeSink } = await import('./audio-track')

    const amplitudes: number[] = []
    const unregister = registerAmplitudeSink(amp => amplitudes.push(amp))

    const playback = playDataUrl('data:audio/mpeg;base64,ended-test')
    await vi.waitFor(() => expect(hoisted.events).toContain('source:data:audio/mpeg;base64,ended-test'))

    expect(rafCallbacks.size).toBeGreaterThan(0)

    FakeAudio.instances[0].emit('ended')
    await expect(playback).resolves.toBe(true)

    expect(rafCallbacks.size).toBe(0)
    expect(amplitudes[amplitudes.length - 1]).toBe(0)

    unregister()
  })
})

describe('warmAudioContext', () => {
  it('synchronously creates AudioContext and fire-and-forget resumes when suspended', async () => {
    let resolveResume: (() => void) | undefined
    hoisted.resumeGate = () => new Promise<void>(resolve => (resolveResume = resolve))

    const { warmAudioContext } = await import('./audio-track')

    warmAudioContext()

    await vi.waitFor(() => expect(hoisted.events).toContain('resume'))

    resolveResume?.()
    await new Promise(r => setTimeout(r, 0))
  })

  it('does not call resume when AudioContext is already running', async () => {
    let resolveResume: (() => void) | undefined
    hoisted.resumeGate = () => new Promise<void>(resolve => (resolveResume = resolve))

    const { warmAudioContext } = await import('./audio-track')

    warmAudioContext()
    resolveResume?.()
    await new Promise(r => setTimeout(r, 0))

    hoisted.events.length = 0

    warmAudioContext()

    expect(hoisted.events).not.toContain('resume')
  })

  it('is idempotent across multiple calls and does not recreate the AudioContext', async () => {
    let resolveResume: (() => void) | undefined
    hoisted.resumeGate = () => new Promise<void>(resolve => (resolveResume = resolve))

    const { warmAudioContext } = await import('./audio-track')

    warmAudioContext()
    warmAudioContext()
    warmAudioContext()

    await vi.waitFor(() => expect(hoisted.events).toContain('resume'))

    resolveResume?.()
    await new Promise(r => setTimeout(r, 0))
  })
})
