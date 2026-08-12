const fs = require('node:fs')
const path = require('node:path')
const yaml = require('yaml')

// Main process computes `compatible` per skill; renderer has no
// `process.platform` and uses the flag to hide non-matching rows.
const HOST_PLATFORM = (() => {
  switch (process.platform) {
    case 'darwin':
      return 'macos'
    case 'win32':
      return 'windows'
    default:
      return process.platform
  }
})()

const PLATFORM_ALIASES = { macos: 'macos', darwin: 'macos', windows: 'windows', win32: 'windows' }

function platformMatches(declared) {
  if (declared == null) return true
  // YAML scalar (`platforms: macos`) or one-element list (`platforms: [macos]`).
  const list = Array.isArray(declared) ? declared : [declared]
  if (list.length === 0) return true
  const mapped = list.map(p => PLATFORM_ALIASES[String(p).toLowerCase()] || String(p).toLowerCase())
  return mapped.includes(HOST_PLATFORM)
}

function listSkillsFromDisk(skillsRoot) {
  if (!skillsRoot) return []
  const skills = []
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
          let platforms = null
          try {
            const content = fs.readFileSync(mdPath, 'utf8')
            const match = content.match(/^---\s*[\r\n]+([\s\S]*?)[\r\n]+---/)
            if (match) {
              const frontmatter = yaml.parse(match[1])
              if (frontmatter.name) name = frontmatter.name
              if (frontmatter.description) description = frontmatter.description
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
            name,
            description,
            platforms,
            compatible: platformMatches(platforms)
          })
        }
      }
    }
  } catch {
    // ignore
  }
  return skills.sort((a, b) => {
    if (a.category !== b.category) return a.category.localeCompare(b.category)
    return a.name.localeCompare(b.name)
  })
}

// Field enumeration (not `...skill`) so internal-only fields added to
// listSkillsFromDisk don't leak to the renderer.
function projectSummary(skill, disabledSet) {
  return {
    category: skill.category,
    name: skill.name,
    description: skill.description,
    platforms: skill.platforms,
    compatible: skill.compatible,
    enabled: !disabledSet.has(skill.name)
  }
}

function buildSkillSummaries(skillsRoot, disabledSet) {
  return listSkillsFromDisk(skillsRoot).map(skill => projectSummary(skill, disabledSet))
}

module.exports = {
  listSkillsFromDisk,
  buildSkillSummaries,
  HOST_PLATFORM
}
