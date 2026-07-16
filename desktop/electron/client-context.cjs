/**
 * Build the `client_context` payload the desktop sends to Backend at login.
 *
 * Backend runs in a cloud container and cannot reach the host OS, so each
 * desktop must volunteer platform / arch / release / version and the list of
 * locally-installed skills (enabled-only — desktop is ground truth for what
 * the runner exposes; runner refuses disabled tool calls independently).
 * Backend stamps this into the JWT `ctx` claim and reads it back on every
 * chat turn (see `backend/core/system_prompt.py` — `client_ctx.skills` /
 * `environment_hints` / `platform_hints`). Without this payload Backend
 * cannot route tool calls correctly: it would fall back to `PLATFORM_HINTS["webui"]`
 * and ship zero skills.
 *
 * Pure (no electron, no fs unless asked). Unit-testable.
 */

const os = require('node:os')
const path = require('node:path')

const { buildSkillSummaries } = require('./lib/skill-index.cjs')

const SCHEMA_VERSION = 1

function resolveZastHome(env = process.env) {
  if (env.ZAST_HOME && String(env.ZAST_HOME).trim()) {
    return path.resolve(String(env.ZAST_HOME))
  }

  // Mirror installer scripts:
  //   POSIX:    $HOME/.zast
  //   Windows:  %LOCALAPPDATA%\zast
  const home = os.homedir()
  if (process.platform === 'win32' && env.LOCALAPPDATA) {
    return path.join(env.LOCALAPPDATA, 'zast')
  }
  return path.join(home, '.zast')
}

function buildClientContext(options = {}) {
  const platform = options.platform ?? process.platform
  const arch = options.arch ?? process.arch
  const release = options.release ?? os.release()
  const nodeVersion = options.nodeVersion ?? process.versions?.node ?? null
  const desktopVersion = options.desktopVersion ?? 'unknown'
  const userAgent = options.userAgent ?? null
  const zastHome = options.zastHome ?? resolveZastHome(options.env)
  const skillsRoot = options.skillsRoot ?? path.join(zastHome, 'skills')
  const configPath = options.configPath ?? path.join(zastHome, 'config.yaml')
  const listSkills = options.listSkills ?? buildSkillSummaries

  const lines = [
    `${platform} ${release}`,
    `arch=${arch}`,
    desktopVersion !== 'unknown' ? `zast-desktop=${desktopVersion}` : null,
    nodeVersion ? `node=${nodeVersion}` : null,
    zastHome ? `zast_home=${zastHome}` : null
  ].filter(Boolean)

  const enabledNames = listSkills({ skillsRoot, configPath })
    // Skills tagged for a different OS are filtered here so the backend's
    // "Enabled local skills…" prompt block (system_prompt.py:64-68) never
    // lists an unavailable skill. Runtime filtering also happens at the
    // runner (skill_matches_platform); this is the prompt-side gate.
    .filter(s => s.enabled && s.compatible)
    .map(s => s.name)

  return {
    schemaVersion: SCHEMA_VERSION,
    client_version: desktopVersion,
    client_context: {
      environment_hints: lines.join('; '),
      platform_hints: userAgent || `ZastDesktop/${desktopVersion} (${platform}; ${arch})`,
      skills: enabledNames
    }
  }
}

module.exports = {
  SCHEMA_VERSION,
  buildClientContext,
  resolveZastHome
}
