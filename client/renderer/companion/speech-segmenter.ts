/**
 * 流式文本 → TTS 句子切分器：句末标点成段，超长强切，成句时清洗 Markdown。
 * 移植自 backend/services/voice/segmenter.py。
 */

export const DEFAULT_MAX_SEGMENT_CHARS = 120

const CJK_ENDINGS = '。！？；…'
const ASCII_ENDINGS = '.!?'
const SOFT_BREAKS = ' ，、,;；'

const MARKDOWN_PATTERNS: readonly [RegExp, string][] = [
  [/\*\*(.+?)\*\*/g, '$1'],
  [/`([^`]*)`/g, '$1'],
  [/\[([^\]]+)\]\([^)]*\)/g, '$1'],
  [/^[-*]\s+/gm, '']
]

/**
 * 剥离常见 Markdown 标记（加粗、行内代码、超链接、行首无序列表标记）。
 */
export function speakable(text: string): string {
  let result = text

  for (const [pattern, repl] of MARKDOWN_PATTERNS) {
    result = result.replace(pattern, repl)
  }

  return result
}

export class SentenceSegmenter {
  private readonly _max: number
  private _buf = ''

  constructor(maxChars = DEFAULT_MAX_SEGMENT_CHARS) {
    this._max = Math.max(8, maxChars)
  }

  /**
   * 增量喂入 chunk，返回本次触发切出的完整可读句子列表。
   */
  feed(chunk: string): string[] {
    this._buf += chunk

    return this._drain(false)
  }

  /**
   * 收尾排干残存缓冲区并清空，返回收尾句子。
   */
  flush(): string[] {
    const segments = this._drain(true)

    if (this._buf) {
      segments.push(this._buf)
      this._buf = ''
    }

    return segments.map(seg => speakable(seg).trim()).filter(Boolean)
  }

  private _drain(final: boolean): string[] {
    const segments: string[] = []

    while (true) {
      const cut = this._sentenceCut(final)

      if (cut <= 0) {
        break
      }

      segments.push(this._buf.slice(0, cut))
      this._buf = this._stripLeadingSoftBreaks(this._buf.slice(cut))
    }

    // 无标点长串：超限即在窗口内最靠后的软分隔符处强切，否则硬切。
    while (this._buf.length > this._max) {
      const window = this._buf.slice(0, this._max)
      let soft = -1

      for (const ch of SOFT_BREAKS) {
        const idx = window.lastIndexOf(ch)

        if (idx > soft) {
          soft = idx
        }
      }

      const cut = soft > Math.floor(this._max / 2) ? soft : this._max
      segments.push(window.slice(0, cut))
      this._buf = this._stripLeadingSoftBreaks(this._buf.slice(cut))
    }

    return segments.map(seg => speakable(seg).trim()).filter(Boolean)
  }

  private _sentenceCut(final: boolean): number {
    for (let i = 0; i < this._buf.length; i++) {
      const ch = this._buf[i]

      if (CJK_ENDINGS.includes(ch)) {
        return i + 1
      }

      if (ASCII_ENDINGS.includes(ch)) {
        const nxt = i + 1 < this._buf.length ? this._buf[i + 1] : null

        if (nxt === null && !final) {
          return -1
        }

        if (nxt !== null && /[0-9a-zA-Z]/.test(nxt)) {
          continue
        }

        return i + 1
      }
    }

    return -1
  }

  private _stripLeadingSoftBreaks(str: string): string {
    let start = 0

    while (start < str.length && SOFT_BREAKS.includes(str[start])) {
      start++
    }

    return str.slice(start)
  }
}
