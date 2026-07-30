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
      return process.platform // 'linux' and unknowns pass through
  }
})()

// Aliases accept either human name (macos/windows/linux) or Node's literal
// (darwin/win32/linux); both must work since frontmatter authors vary.
const PLATFORM_ALIASES = { macos: 'macos', darwin: 'macos', windows: 'windows', win32: 'windows', linux: 'linux' }

function platformMatches(declared) {
  if (declared == null) return true
  // YAML scalar (`platforms: macos`) or one-element list (`platforms: [macos]`).
  const list = Array.isArray(declared) ? declared : [declared]
  if (list.length === 0) return true
  const mapped = list.map(p => PLATFORM_ALIASES[String(p).toLowerCase()] || String(p).toLowerCase())
  return mapped.includes(HOST_PLATFORM)
}

// Mtime-keyed cache for `<section>.disabled` so per-login buildClientContext,
// per-IPC `deskagent:skills:list`, and per-IPC `deskagent:toolsets:list` calls don't
// re-parse $DESKAGENT_HOME/config.yaml on every invocation. Per-section state so
// skills.disabled and toolsets.disabled don't collide. One stat per call to
// detect writes.
const cachedBySection = new Map()

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

function readDisabledSet(configPath, section = 'skills') {
  if (!configPath) return new Set()
  try {
    const mtime = fs.statSync(configPath).mtimeMs
    const cache = cachedBySection.get(section)
    if (cache && cache.path === configPath && cache.mtime === mtime) return cache.set
    const doc = yaml.parse(fs.readFileSync(configPath, 'utf8'))
    const arr = Array.isArray(doc?.[section]?.disabled) ? doc[section].disabled : []
    const next = new Set(arr.map(String))
    cachedBySection.set(section, { path: configPath, mtime, set: next })
    return next
  } catch {
    return new Set()
  }
}

// Mtime granularity is coarse on some filesystems (or two writes land in the
// same tick); a stat-only cache could return the pre-write set for the next
// read after a write. Writers call this immediately after a successful
// atomic write so the next readDisabledSet unconditionally re-parses.
function invalidateDisabledCache(section) {
  if (section === undefined) {
    cachedBySection.clear()

    return
  }

  cachedBySection.delete(section)
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

// ``disabledSet`` is the mtime-cached value from readDisabledSet for the
// ``deskagent:skills:list`` path, or the post-write set returned by
// patchAndCommit for the ``deskagent:skill:set-enabled`` path (avoids a second
// disk read).
function buildSkillSummaries(skillsRoot, disabledSet) {
  return listSkillsFromDisk(skillsRoot).map(skill => projectSummary(skill, disabledSet))
}

module.exports = {
  listSkillsFromDisk,
  readDisabledSet,
  buildSkillSummaries,
  invalidateDisabledCache,
  HOST_PLATFORM
}
