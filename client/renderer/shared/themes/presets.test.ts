import { describe, expect, it } from 'vitest'

import { DEFAULT_TYPOGRAPHY, spiritagentTheme } from './presets'

// #40364：所有 UI 正文 / 等宽字体都不携带 emoji 字形，因此每个字体栈
// 必须以彩色 emoji 字体兜底，否则在默认字体不含 emoji 的平台上
// 会渲染为豆腐块。
describe('theme typography emoji fallback (#40364)', () => {
  const stacks: Array<[string, string]> = (
    [
      ['DEFAULT_TYPOGRAPHY.fontSans', DEFAULT_TYPOGRAPHY.fontSans],
      ['DEFAULT_TYPOGRAPHY.fontMono', DEFAULT_TYPOGRAPHY.fontMono],
      // A theme may override only fontMono (fontSans then falls back to the
      // default, which already carries the emoji stack), so skip undefined.
      [`${spiritagentTheme.name}.fontSans`, spiritagentTheme.typography?.fontSans],
      [`${spiritagentTheme.name}.fontMono`, spiritagentTheme.typography?.fontMono]
    ] as Array<[string, string | undefined]>
  ).filter((entry): entry is [string, string] => typeof entry[1] === 'string')

  it.each(stacks)('%s includes a color-emoji font', (_label, stack) => {
    expect(stack).toMatch(/Apple Color Emoji|Segoe UI Emoji|Noto Color Emoji|(^|,\s*)emoji\b/)
  })
})
