import { describe, expect, it } from 'vitest'

import { classifyFocusedApp } from './activity'

describe('classifyFocusedApp', () => {
  it('returns "unknown" for empty / missing info', () => {
    expect(classifyFocusedApp({})).toBe('unknown')
  })

  it('classifies Windows IDE apps', () => {
    // Test the underlying classification regardless of detected platform —
    // the platform gate lives in the activity.ts runner; here we exercise
    // the data-driven allowlist via a synthetic Windows browser env.
    const originalPlatform = (navigator as Navigator).platform
    Object.defineProperty(navigator, 'platform', { value: 'Win32', configurable: true })

    try {
      expect(classifyFocusedApp({ name: 'Code.exe' })).toBe('ide')
      expect(classifyFocusedApp({ name: 'idea64.exe' })).toBe('ide')
      expect(classifyFocusedApp({ name: 'sublime_text.exe' })).toBe('ide')
      expect(classifyFocusedApp({ name: 'notepad.exe' })).toBe('unknown')
    } finally {
      Object.defineProperty(navigator, 'platform', { value: originalPlatform, configurable: true })
    }
  })

  it('classifies Windows media and reader apps', () => {
    Object.defineProperty(navigator, 'platform', { value: 'Win32', configurable: true })

    try {
      expect(classifyFocusedApp({ name: 'Spotify.exe' })).toBe('music')
      expect(classifyFocusedApp({ name: 'Acrobat.exe' })).toBe('reader')
      expect(classifyFocusedApp({ name: 'chrome.exe' })).toBe('browsing')
      expect(classifyFocusedApp({ name: 'steam.exe' })).toBe('gaming')
    } finally {
      Object.defineProperty(navigator, 'platform', { value: '', configurable: true })
    }
  })

  it('classifies macOS bundle prefixes', () => {
    Object.defineProperty(navigator, 'platform', { value: 'MacIntel', configurable: true })

    try {
      expect(classifyFocusedApp({ bundle: 'com.microsoft.VSCode', name: 'Visual Studio Code' })).toBe('ide')
      expect(classifyFocusedApp({ bundle: 'com.spotify.client', name: 'Spotify' })).toBe('music')
      expect(classifyFocusedApp({ bundle: 'com.adobe.Acrobat', name: 'Adobe Acrobat' })).toBe('reader')
      expect(classifyFocusedApp({ bundle: 'com.valvesoftware.steam', name: 'Steam' })).toBe('gaming')
      expect(classifyFocusedApp({ bundle: 'com.google.Chrome', name: 'Google Chrome' })).toBe('browsing')
    } finally {
      Object.defineProperty(navigator, 'platform', { value: '', configurable: true })
    }
  })

  it('classifies Linux class names via name/title substrings', () => {
    Object.defineProperty(navigator, 'platform', { value: 'Linux x86_64', configurable: true })

    try {
      expect(classifyFocusedApp({ name: 'code' })).toBe('ide')
      expect(classifyFocusedApp({ name: 'spotify', title: 'Spotify' })).toBe('music')
      expect(classifyFocusedApp({ name: 'zathura', title: 'document.pdf' })).toBe('reader')
      expect(classifyFocusedApp({ name: 'steam', title: 'Library' })).toBe('gaming')
      expect(classifyFocusedApp({ name: 'firefox', title: 'Mozilla Firefox' })).toBe('browsing')
    } finally {
      Object.defineProperty(navigator, 'platform', { value: '', configurable: true })
    }
  })

  it('returns "unknown" for unrecognised processes', () => {
    Object.defineProperty(navigator, 'platform', { value: 'Win32', configurable: true })

    try {
      expect(classifyFocusedApp({ name: 'mystery_process.exe' })).toBe('unknown')
    } finally {
      Object.defineProperty(navigator, 'platform', { value: '', configurable: true })
    }
  })
})
