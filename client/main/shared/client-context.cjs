const os = require('node:os')
const path = require('node:path')

const { buildSkillSummaries } = require('./lib/skill-index.cjs')
const store = require('./lib/runner-config-store.cjs')

const SCHEMA_VERSION = 1

function resolveDeskAgentHome(env = process.env) {
  if (env.DESKAGENT_HOME && String(env.DESKAGENT_HOME).trim()) {
    return path.resolve(String(env.DESKAGENT_HOME))
  }

  // Mirror installer scripts:
  //   POSIX:    $HOME/.deskagent
  //   Windows:  %LOCALAPPDATA%\deskagent
  const home = os.homedir()
  if (process.platform === 'win32' && env.LOCALAPPDATA) {
    return path.join(env.LOCALAPPDATA, 'deskagent')
  }
  return path.join(home, '.deskagent')
}

function buildClientContext(options = {}) {
  const platform = options.platform ?? process.platform
  const arch = options.arch ?? process.arch
  const release = options.release ?? os.release()
  const nodeVersion = options.nodeVersion ?? process.versions?.node ?? null
  const desktopVersion = options.desktopVersion ?? 'unknown'
  const userAgent = options.userAgent ?? null
  const deskagentHome = options.deskagentHome ?? resolveDeskAgentHome(options.env)
  const skillsRoot = options.skillsRoot ?? path.join(deskagentHome, 'skills')
  const listSkills = options.listSkills ?? buildSkillSummaries

  const lines = [
    `${platform} ${release}`,
    `arch=${arch}`,
    desktopVersion !== 'unknown' ? `deskagent-desktop=${desktopVersion}` : null,
    nodeVersion ? `node=${nodeVersion}` : null,
    deskagentHome ? `deskagent_home=${deskagentHome}` : null
  ].filter(Boolean)

  const enabledNames = listSkills(skillsRoot, store.getDisabledSet())
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
      platform_hints: userAgent || `DeskAgentDesktop/${desktopVersion} (${platform}; ${arch})`,
      skills: enabledNames
    }
  }
}

module.exports = {
  SCHEMA_VERSION,
  buildClientContext,
  resolveDeskAgentHome
}
