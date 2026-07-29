const fs = require('node:fs')

function isWslEnvironment(env = process.env, platform = process.platform, kernelRelease = null) {
  if (platform !== 'linux') return false
  if (env.WSL_DISTRO_NAME || env.WSL_INTEROP) return true

  try {
    const release = kernelRelease ?? fs.readFileSync('/proc/sys/kernel/osrelease', 'utf8')
    return /microsoft|wsl/i.test(release)
  } catch {
    return false
  }
}

const GPU_OVERRIDE_ON = new Set(['1', 'true', 'yes', 'on'])
const GPU_OVERRIDE_OFF = new Set(['0', 'false', 'no', 'off'])

/**
 * Decide whether the app is shown over a remote/forwarded display where
 * Chromium's GPU compositor produces an unstable, flickering surface.
 * Returns a short reason string when GPU should be disabled, or null.
 * `DESKAGENT_DESKTOP_DISABLE_GPU` overrides detection.
 * Pure + dependency-free so it can be unit-tested.
 */
function detectRemoteDisplay(options = {}) {
  const env = options.env ?? process.env
  const platform = options.platform ?? process.platform

  const override = String(env.DESKAGENT_DESKTOP_DISABLE_GPU || '')
    .trim()
    .toLowerCase()
  if (GPU_OVERRIDE_ON.has(override)) return 'override (DESKAGENT_DESKTOP_DISABLE_GPU)'
  if (GPU_OVERRIDE_OFF.has(override)) return null

  // Launched from an SSH session → display is X11-forwarded or remote.
  if (env.SSH_CONNECTION || env.SSH_CLIENT || env.SSH_TTY) return 'ssh-session'

  if (platform === 'linux') {
    // X11 forwarding: DISPLAY "<host>:N" (e.g. "localhost:10.0"); local is ":0"/":1".
    const display = String(env.DISPLAY || '')
    if (display.includes(':') && display.split(':')[0]) {
      return `x11-forwarding (DISPLAY=${display})`
    }
  }

  if (platform === 'win32') {
    // RDP sessions report SESSIONNAME like "RDP-Tcp#7"; local console is "Console".
    const sessionName = String(env.SESSIONNAME || '')
    if (/^rdp-/i.test(sessionName)) return `rdp (SESSIONNAME=${sessionName})`
  }

  return null
}

module.exports = {
  detectRemoteDisplay,
  isWslEnvironment
}
