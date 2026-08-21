import { atom } from 'nanostores'

import { safeJsonParse } from '@/shared/lib/safe-json'

import { personaFromWire } from './persona-mappers'

export interface PersonaDefinition {
  name: string
  personality: string
  speakingStyle: string
  background?: string
  biological_type?: string
  gender?: string
  // appearance：外貌特征（脸 / 体型 / 标志性细节）。
  appearance?: string
}

export const $persona = atom<PersonaDefinition | null>(null)
export const $personalityTags = atom<string[]>([])

export async function hydratePersona(opts: { silent?: boolean } = {}): Promise<{ ok: boolean; error?: unknown }> {
  try {
    // 全部结构化 persona 字段都在 definition_json（JSON 字符串 blob）里面，
    // 而不是作为顶层扁平 key 出现在线协议里。
    const p = await window.spiritagent.api<{
      definition_json?: string
      is_complete?: boolean
      personality_tags?: string[]
    }>({
      path: '/api/companion/persona'
    })

    if (!p?.is_complete) {
      // 「还没设置 persona」是合法状态，不是错误：保持 $persona 不动（不要置空），
      // 这样「保存刚刚成功，hydrate 落地却读到陈旧 is_complete」的竞态，
      // 不会让那些依赖 $persona 的消费者把它当成清空。
      return { ok: true }
    }

    const parsed = safeJsonParse<Record<string, string>>(p.definition_json, {})

    $persona.set(
      personaFromWire({
        name: parsed.name ?? '伙伴',
        personality: parsed.personality ?? '',
        speaking_style: parsed.speaking_style,
        background: parsed.background,
        biological_type: parsed.biological_type,
        gender: parsed.gender,
        appearance: parsed.appearance
      })
    )

    $personalityTags.set(p.personality_tags ?? [])

    return { ok: true }
  } catch (err) {
    // C2：调用方刚刚成功 PUT 了新 persona 时，这里的 GET 短暂失败不代表保存失败——
    // 后端是有数据的。传 `silent: true` 保持 $persona 不动，避免同时弹出「保存失败」提示
    // 又让设置页因为 $persona 变 null 而隐藏「编辑」按钮。GET 失败由调用方作为软提示暴露。
    if (!opts.silent) {
      $persona.set(null)
      $personalityTags.set([])
    }

    return { ok: false, error: err }
  }
}
