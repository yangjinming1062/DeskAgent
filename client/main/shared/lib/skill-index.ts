import fs from 'node:fs'
import path from 'node:path'

import yaml from 'yaml'

import type { SkillItem } from '../ipc-contracts'

interface RawSkillItem {
  category: string
  compatible: boolean
  description?: string
  name: string
  platforms?: string[] | null
}

// Main process computes `compatible` per skill; renderer has no
// `process.platform` and uses the flag to hide non-matching rows.
const HOST_PLATFORM: string = (() => {
  switch (process.platform) {
    case 'darwin':
      return 'macos'

    case 'win32':
      return 'windows'

    default:
      return process.platform
  }
})()

const PLATFORM_ALIASES: Record<string, string> = {
  darwin: 'macos',
  macos: 'macos',
  win32: 'windows',
  windows: 'windows'
}

function platformMatches(declared?: null | string | string[]): boolean {
  if (declared == null) {
    return true
  }

  // YAML scalar (`platforms: macos`) or one-element list (`platforms: [macos]`).
  const list = Array.isArray(declared) ? declared : [declared]

  if (list.length === 0) {
    return true
  }

  const mapped = list.map(p => PLATFORM_ALIASES[String(p).toLowerCase()] || String(p).toLowerCase())

  return mapped.includes(HOST_PLATFORM)
}

function listSkillsFromDisk(skillsRoot?: null | string): RawSkillItem[] {
  if (!skillsRoot) {
    return []
  }

  const skills: RawSkillItem[] = []

  try {
    const categories = fs
      .readdirSync(skillsRoot, { withFileTypes: true })
      .filter(e => e.isDirectory() || e.isSymbolicLink())

    for (const category of categories) {
      const categoryPath = path.join(skillsRoot, category.name)

      const skillDirs = fs
        .readdirSync(categoryPath, { withFileTypes: true })
        .filter(e => e.isDirectory() || e.isSymbolicLink())

      for (const skillDir of skillDirs) {
        const skillPath = path.join(categoryPath, skillDir.name)
        const mdPath = path.join(skillPath, 'SKILL.md')

        if (fs.existsSync(mdPath)) {
          let name = skillDir.name
          let description = ''
          let platforms: null | string[] = null

          try {
            const content = fs.readFileSync(mdPath, 'utf8')
            const match = content.match(/^---\s*[\r\n]+([\s\S]*?)[\r\n]+---/)

            if (match) {
              const frontmatter = yaml.parse(match[1])

              if (frontmatter.name) {
                name = frontmatter.name
              }

              if (frontmatter.description) {
                description = frontmatter.description
              }

              if (frontmatter.platforms != null) {
                const raw = frontmatter.platforms
                platforms = (Array.isArray(raw) ? raw : [raw]).map(String)
              }
            }
          } catch {
            // ignore parsing errors
          }

          skills.push({
            category: category.name,
            compatible: platformMatches(platforms),
            description,
            name,
            platforms
          })
        }
      }
    }
  } catch {
    // ignore
  }

  return skills.sort((a, b) => {
    if (a.category !== b.category) {
      return a.category.localeCompare(b.category)
    }

    return a.name.localeCompare(b.name)
  })
}

// Field enumeration (not `...skill`) so internal-only fields added to
// listSkillsFromDisk don't leak to the renderer.
function projectSummary(skill: RawSkillItem, disabledSet: Set<string>): SkillItem {
  return {
    category: skill.category,
    compatible: skill.compatible,
    description: skill.description,
    enabled: !disabledSet.has(skill.name),
    name: skill.name,
    platforms: skill.platforms
  }
}

export function buildSkillSummaries(skillsRoot: null | string | undefined, disabledSet: Set<string>): SkillItem[] {
  return listSkillsFromDisk(skillsRoot).map(skill => projectSummary(skill, disabledSet))
}
