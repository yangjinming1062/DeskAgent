import type { ReactionBucket, ReactionEntry, ReactionTone } from '@/shared/types/reactions'

import { isLatestGen, nextGen, playDataUrl } from '../audio-track'
import { $companionVoiceId } from '../prefs'

import manifestJson from './manifest.json'

interface ManifestShape {
  language: string
  format: 'mp3'
  buckets: readonly ReactionBucket[]
  tones: readonly ReactionTone[]
  files: readonly { tag: string; tone: ReactionTone; bucket: ReactionBucket; text: string }[]
}

const MANIFEST = manifestJson as ManifestShape

const VALID_TONE: ReadonlySet<ReactionTone> = new Set(MANIFEST.tones)
const VALID_BUCKET: ReadonlySet<ReactionBucket> = new Set(MANIFEST.buckets)

// `Map<bucket, Map<tone, ReactionEntry[]>>` — fast random pick given (bucket, tone).
const index: Map<ReactionBucket, Map<ReactionTone, ReactionEntry[]>> = (() => {
  const byBucket = new Map<ReactionBucket, Map<ReactionTone, ReactionEntry[]>>()

  for (const file of MANIFEST.files) {
    if (!VALID_TONE.has(file.tone) || !VALID_BUCKET.has(file.bucket)) {
      continue
    }

    let byTone = byBucket.get(file.bucket)

    if (!byTone) {
      byTone = new Map<ReactionTone, ReactionEntry[]>()
      byBucket.set(file.bucket, byTone)
    }

    let list = byTone.get(file.tone)

    if (!list) {
      list = []
      byTone.set(file.tone, list)
    }

    list.push({ tag: file.tag, tone: file.tone, bucket: file.bucket, text: file.text })
  }

  return byBucket
})()

export function hasManifest(): boolean {
  return MANIFEST.files.length > 0
}

export function pickReaction(bucket: ReactionBucket, tone: ReactionTone): ReactionEntry | null {
  const list = index.get(bucket)?.get(tone)

  return list && list.length > 0 ? list[Math.floor(Math.random() * list.length)] : null
}

// Tracks tags currently being processed by `backgroundBakeReactions` (or any
// single-entry auto-bake) so a per-poke fallback doesn't fire a duplicate
// cloud TTS for a tag the bulk bake is already synthesising.
const inflightBakeTags = new Set<string>()

export interface PlayReactionOpts {
  tone: ReactionTone
  bucket: ReactionBucket
  userInitiated: boolean
}

export async function playReactionAudio(entry: ReactionEntry | null, opts: PlayReactionOpts): Promise<boolean> {
  const gen = nextGen()

  if (entry) {
    try {
      const res = await window.deskagent.media.reactionAudio.read(entry.tag)

      if (!isLatestGen(gen)) {
        return false
      }

      return await playDataUrl(res.dataUrl)
    } catch (readErr) {
      console.info('[reaction-audio] local clip missing for', entry.tag, '— falling back to cloud TTS + auto-bake')
    }
  }

  // Fallback path: missing local mp3, or empty manifest (rare — build
  // misconfiguration). Always uses the user's chosen voice; auto-bakes the
  // entry to disk so subsequent pokes hit the local file.
  const voice = $companionVoiceId.get()
  void playOnce(entry, voice)

  if (entry && !inflightBakeTags.has(entry.tag)) {
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
    console.warn('[reaction-audio] runtime TTS fallback failed', entry?.tag ?? '(no entry)', err)
  }
}

async function autoBake(entries: ReactionEntry[], voice: string): Promise<void> {
  const newTags = entries.filter(e => !inflightBakeTags.has(e.tag))

  if (newTags.length === 0) {
    return
  }

  newTags.forEach(e => inflightBakeTags.add(e.tag))

  try {
    const resolvedVoice = voice || $companionVoiceId.get()

    if (!resolvedVoice) {
      return
    }

    const { results } = await window.deskagent.media.reactionAudio.generate({
      voice: resolvedVoice,
      language: 'zh',
      entries: newTags.map(e => ({ tag: e.tag, text: e.text, tone: e.tone, bucket: e.bucket }))
    })

    for (const r of results) {
      if (r.ok) {
        console.info('[reaction-audio] auto-baked', r.tag, r.bytes, 'bytes')
      } else {
        console.warn('[reaction-audio] auto-bake failed for', r.tag, r.reason)
      }
    }
  } catch (err) {
    console.warn('[reaction-audio] auto-bake IPC failed', err)
  } finally {
    newTags.forEach(e => inflightBakeTags.delete(e.tag))
  }
}

export async function backgroundBakeReactions({ voice }: { voice: string }): Promise<void> {
  if (!hasManifest() || !voice) {
    return
  }

  const entries: ReactionEntry[] = []

  for (const [, byTone] of index) {
    for (const [, list] of byTone) {
      for (const e of list) {
        entries.push(e)
      }
    }
  }

  for (const e of entries) {
    inflightBakeTags.add(e.tag)
  }

  try {
    const { results } = await window.deskagent.media.reactionAudio.generate({
      voice,
      language: 'zh',
      entries: entries.map(e => ({ tag: e.tag, text: e.text, tone: e.tone, bucket: e.bucket }))
    })

    const ok = results.filter(r => r.ok).length
    console.info(`[reaction-audio] background bake complete: baked=${ok}/${results.length}, voice=${voice}`)
  } catch (err) {
    console.warn('[reaction-audio] background bake failed', err)
  } finally {
    for (const e of entries) {
      inflightBakeTags.delete(e.tag)
    }
  }
}
