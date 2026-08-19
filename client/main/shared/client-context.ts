import os from 'node:os'
import path from 'node:path'

import { spiritagentHome as resolveSpiritAgentHome } from '../security/paths'

import * as store from './lib/runner-config-store'
import { buildSkillSummaries } from './lib/skill-index'

const SCHEMA_VERSION = 1

interface BuildClientContextOptions {
  arch?: string
  spiritagentHome?: null | string
  desktopVersion?: string
  listSkills?: typeof buildSkillSummaries
  nodeVersion?: null | string
  platform?: string
  release?: string
  skillsRoot?: string
  userAgent?: null | string
}

interface ClientContextResult {
  client_context: {
    environment_hints: string
    platform_hints: string
    skills: string[]
  }
  client_version: string
  schemaVersion: number
}

export function buildClientContext(options: BuildClientContextOptions = {}): ClientContextResult {
  const platform = options.platform ?? process.platform
  const arch = options.arch ?? process.arch
  const release = options.release ?? os.release()
  const nodeVersion = options.nodeVersion ?? process.versions?.node ?? null
  const desktopVersion = options.desktopVersion ?? 'unknown'
  const userAgent = options.userAgent ?? null
  const spiritagentHome = options.spiritagentHome ?? resolveSpiritAgentHome()
  const skillsRoot = options.skillsRoot ?? (spiritagentHome ? path.join(spiritagentHome, 'skills') : '')
  const listSkills = options.listSkills ?? buildSkillSummaries

  const lines = [
    `${platform} ${release}`,
    `arch=${arch}`,
    desktopVersion !== 'unknown' ? `spiritagent-desktop=${desktopVersion}` : null,
    nodeVersion ? `node=${nodeVersion}` : null,
    spiritagentHome ? `spiritagent_home=${spiritagentHome}` : null
  ].filter(Boolean)

  const enabledNames = listSkills(skillsRoot, store.getDisabledSet())
    // Skills tagged for a different OS are filtered here so the backend's
    // "Enabled local skills…" prompt block (system_prompt.py:64-68) never
    // lists an unavailable skill. Runtime filtering also happens at the
    // runner (skill_matches_platform); this is the prompt-side gate.
    .filter(s => s.enabled && s.compatible)
    .map(s => s.name)

  return {
    client_context: {
      environment_hints: lines.join('; '),
      platform_hints: userAgent || `SpiritAgentDesktop/${desktopVersion} (${platform}; ${arch})`,
      skills: enabledNames
    },
    client_version: desktopVersion,
    schemaVersion: SCHEMA_VERSION
  }
}
