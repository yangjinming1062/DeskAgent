import type { ReactionBucket, ReactionEntry } from '@/shared/types/reactions'

import { isLatestGen, nextGen, playDataUrl } from '../audio-track'
import { $companionVoiceId } from '../prefs'

import manifestJson from './manifest.json'

interface ManifestShape {
  language: string
  format: 'mp3'
  buckets: readonly ReactionBucket[]
  files: readonly { id: string; tags: readonly string[]; bucket: ReactionBucket; text: string }[]
}

const MANIFEST = manifestJson as ManifestShape

const VALID_BUCKET: ReadonlySet<ReactionBucket> = new Set(MANIFEST.buckets)

// `Map<bucket, ReactionEntry[]>` — fast lookup by reaction bucket.
const index: Map<ReactionBucket, ReactionEntry[]> = (() => {
  const byBucket = new Map<ReactionBucket, ReactionEntry[]>()

  for (const file of MANIFEST.files) {
    if (!VALID_BUCKET.has(file.bucket)) {
      continue
    }

    let list = byBucket.get(file.bucket)

    if (!list) {
      list = []
      byBucket.set(file.bucket, list)
    }

    list.push({ id: file.id, tags: [...file.tags], bucket: file.bucket, text: file.text })
  }

  return byBucket
})()

export function hasManifest(): boolean {
  return MANIFEST.files.length > 0
}

/** 按性格标签交集匹配从指定 bucket 候选中选择反应条目。
 *  交集匹配 > 0 优先；通用条目/同 bucket 条目始终作为候选池的一部分。 */
export function pickReaction(bucket: ReactionBucket, companionTags: string[]): ReactionEntry | null {
  const candidates = index.get(bucket)

  if (!candidates || candidates.length === 0) {
    return null
  }

  if (companionTags.length > 0) {
    const tagSet = new Set(companionTags)

    const scored = candidates.map(e => ({
      entry: e,
      score: e.tags.filter(t => tagSet.has(t)).length
    }))

    const maxScore = Math.max(...scored.map(s => s.score))

    if (maxScore > 0) {
      const topMatches = scored.filter(s => s.score === maxScore).map(s => s.entry)

      return topMatches[Math.floor(Math.random() * topMatches.length)]
    }
  }

  return candidates[Math.floor(Math.random() * candidates.length)]
}

// Tracks IDs currently being processed by `backgroundBakeReactions` (or any
// single-entry auto-bake) so a per-poke fallback doesn't fire a duplicate
// cloud TTS for an ID the bulk bake is already synthesising.
const inflightBakeIds = new Set<string>()

export interface PlayReactionOpts {
  tags?: string[]
  bucket: ReactionBucket
  userInitiated: boolean
}

export async function playReactionAudio(entry: ReactionEntry | null, opts: PlayReactionOpts): Promise<boolean> {
  const gen = nextGen()

  if (entry) {
    try {
      const res = await window.deskagent.media.reactionAudio.read(entry.id)

      if (!isLatestGen(gen)) {
        return false
      }

      return await playDataUrl(res.dataUrl)
    } catch (readErr) {
      console.info('[reaction-audio] local clip missing for', entry.id, '— falling back to cloud TTS + auto-bake')
    }
  }

  // Fallback path: missing local mp3, or empty manifest (rare — build
  // misconfiguration). Always uses the user's chosen voice; auto-bakes the
  // entry to disk so subsequent pokes hit the local file.
  const voice = $companionVoiceId.get()
  void playOnce(entry, voice)

  if (entry && !inflightBakeIds.has(entry.id)) {
    void autoBake([entry], voice)
  }

  return true
}

async function playOnce(entry: ReactionEntry | null, voice: string): Promise<void> {
  const text = entry?.text ?? '嗯？'

  try {
    const res = await window.deskagent.media.tts({
      text,
      voice: voice || undefined,
      context: 'reaction-fallback'
    })

    await playDataUrl(res.dataUrl)
  } catch (err) {
    console.warn('[reaction-audio] runtime TTS fallback failed', entry?.id ?? '(no entry)', err)
  }
}

async function autoBake(entries: ReactionEntry[], voice: string): Promise<void> {
  const newEntries = entries.filter(e => !inflightBakeIds.has(e.id))

  if (newEntries.length === 0) {
    return
  }

  newEntries.forEach(e => inflightBakeIds.add(e.id))

  try {
    const resolvedVoice = voice || $companionVoiceId.get()

    if (!resolvedVoice) {
      return
    }

    const { results } = await window.deskagent.media.reactionAudio.generate({
      voice: resolvedVoice,
      language: 'zh',
      entries: newEntries.map(e => ({ id: e.id, text: e.text, tags: e.tags, bucket: e.bucket }))
    })

    for (const r of results) {
      if (r.ok) {
        console.info('[reaction-audio] auto-baked', r.id, r.bytes, 'bytes')
      } else {
        console.warn('[reaction-audio] auto-bake failed for', r.id, r.reason)
      }
    }
  } catch (err) {
    console.warn('[reaction-audio] auto-bake IPC failed', err)
  } finally {
    newEntries.forEach(e => inflightBakeIds.delete(e.id))
  }
}

export async function backgroundBakeReactions({ voice }: { voice: string }): Promise<void> {
  if (!hasManifest() || !voice) {
    return
  }

  const entries: ReactionEntry[] = []

  for (const [, list] of index) {
    for (const e of list) {
      entries.push(e)
    }
  }

  for (const e of entries) {
    inflightBakeIds.add(e.id)
  }

  try {
    const { results } = await window.deskagent.media.reactionAudio.generate({
      voice,
      language: 'zh',
      entries: entries.map(e => ({ id: e.id, text: e.text, tags: e.tags, bucket: e.bucket }))
    })

    const ok = results.filter(r => r.ok).length
    console.info(`[reaction-audio] background bake complete: baked=${ok}/${results.length}, voice=${voice}`)
  } catch (err) {
    console.warn('[reaction-audio] background bake failed', err)
  } finally {
    for (const e of entries) {
      inflightBakeIds.delete(e.id)
    }
  }
}
