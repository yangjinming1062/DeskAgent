import type { PersonaPayload } from './persona'
import type { PersonaDefinition } from './persona-store'

// Wire↔store mapper pair for the persona shape. PersonaDefinition (camelCase)
// is the renderer-side `$persona` store; PersonaPayload (snake_case) is what
// the backend PUT reads. Adding a new field requires touching exactly one of
// these mappers + the corresponding source-of-truth type.

export function personaFromWire(payload: PersonaPayload): PersonaDefinition {
  return {
    name: payload.name,
    personality: payload.personality,
    speakingStyle: payload.speaking_style,
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
    speaking_style: def.speakingStyle,
    ...(def.background !== undefined && { background: def.background }),
    ...(def.biological_type !== undefined && { biological_type: def.biological_type }),
    ...(def.gender !== undefined && { gender: def.gender }),
    ...(def.appearance !== undefined && { appearance: def.appearance })
  }
}
