import type { PersonaPayload } from './persona'
import type { PersonaDefinition } from './persona-store'

// persona 形状在 wire 与 store 之间的 mapper 对。PersonaDefinition（camelCase）
// 是渲染端的 `$persona` store；PersonaPayload（snake_case）是后端 PUT 所读字段。
// 新增字段只需碰这两个 mapper 之一，再加上对应 source-of-truth 类型即可。

export function personaFromWire(payload: PersonaPayload): PersonaDefinition {
  return {
    name: payload.name,
    personality: payload.personality,
    speakingStyle: payload.speaking_style ?? '',
    ...(payload.background !== undefined && { background: payload.background }),
    ...(payload.biological_type !== undefined && { biological_type: payload.biological_type }),
    ...(payload.gender !== undefined && { gender: payload.gender }),
    ...(payload.appearance !== undefined && { appearance: payload.appearance })
  }
}

export function personaToWire(def: PersonaDefinition): PersonaPayload {
  return {
    name: def.name,
    personality: def.personality,
    speaking_style: def.speakingStyle ?? '',
    ...(def.background !== undefined && { background: def.background }),
    ...(def.biological_type !== undefined && { biological_type: def.biological_type }),
    ...(def.gender !== undefined && { gender: def.gender }),
    ...(def.appearance !== undefined && { appearance: def.appearance })
  }
}
