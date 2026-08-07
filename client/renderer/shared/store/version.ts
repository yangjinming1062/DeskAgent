import { atom } from 'nanostores'

import type { DesktopVersionInfo } from '@/shared/types/global'

// Lazily populated on first read. Refreshed by `refreshDesktopVersion()` (called
// from the About panel on mount so the displayed version reflects the running
// build even after a recent re-launch).
const $desktopVersion = atom<DesktopVersionInfo | null>(null)

async function refreshDesktopVersion() {
  try {
    const next = await window.deskagent?.getVersion()

    if (next) {
      $desktopVersion.set(next)
    }
  } catch {
    // Best-effort; About panel will show the "version unavailable" copy.
  }
}

export { $desktopVersion, refreshDesktopVersion }
