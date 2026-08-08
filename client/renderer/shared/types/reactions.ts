export type ReactionTone = 'gentle' | 'lively' | 'snarky' | 'calm'
export type ReactionBucket = 'poke-light' | 'poke-medium' | 'poke-heavy' | 'drag'

export interface ReactionEntry {
  tag: string
  tone: ReactionTone
  bucket: ReactionBucket
  text: string
}
