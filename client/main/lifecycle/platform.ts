export const GPU_OVERRIDE_ON: Set<string> = new Set(['1', 'true', 'yes', 'on'])
export const GPU_OVERRIDE_OFF: Set<string> = new Set(['0', 'false', 'no', 'off'])

export interface DetectRemoteDisplayOptions {
  env?: NodeJS.ProcessEnv | Record<string, string | undefined>
  platform?: string
}

/**
 * Decide whether the app is shown over a remote/forwarded display where
 * Chromium's GPU compositor produces an unstable, flickering surface.
 * Returns a short reason string when GPU should be disabled, or null.
 * `DESKAGENT_DESKTOP_DISABLE_GPU` overrides detection.
 * Pure + dependency-free so it can be unit-tested.
 */
export function detectRemoteDisplay(options: DetectRemoteDisplayOptions = {}): null | string {
  const env = options.env ?? process.env
  const platform = options.platform ?? process.platform

  const override = String(env.DESKAGENT_DESKTOP_DISABLE_GPU || '')
    .trim()
    .toLowerCase()

  if (GPU_OVERRIDE_ON.has(override)) {
    return 'override (DESKAGENT_DESKTOP_DISABLE_GPU)'
  }

  if (GPU_OVERRIDE_OFF.has(override)) {
    return null
  }

  // SSH-session → display is X11-forwarded or remote.
  if (env.SSH_CONNECTION || env.SSH_CLIENT || env.SSH_TTY) {
    return 'ssh-session'
  }

  if (platform === 'win32') {
    // RDP sessions report SESSIONNAME like "RDP-Tcp#7"; local console is "Console".
    const sessionName = String(env.SESSIONNAME || '')

    if (/^rdp-/i.test(sessionName)) {
      return `rdp (SESSIONNAME=${sessionName})`
    }
  }

  return null
}
