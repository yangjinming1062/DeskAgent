import type { WardrobeItem } from './state/wardrobe-store'

export class ApiError extends Error {
  constructor(public status: number, public body: Record<string, unknown>) {
    super(`API ${status}: ${JSON.stringify(body)}`)
  }
}

export interface TokenResponse {
  access_token: string
  token_type: string
  expires_in: number
  user: { id: number; username: string }
}

export interface PersonaData {
  id: number
  is_complete: boolean
  definition_json: string
}

export interface ModelData {
  id: number
  asset_url: string | null
  provider: string
  species: string
  morph_params: Record<string, number>
  status: string
  has_rig: boolean
  has_morph_targets: boolean
  active: boolean
}

export class ApiClient {
  constructor(private baseUrl: string, private getToken: () => string | null) {}

  private async req<T>(path: string, init?: RequestInit): Promise<T> {
    const token = this.getToken()
    const headers: Record<string, string> = {}
    if (token) headers['Authorization'] = `Bearer ${token}`
    if (!(init?.body instanceof FormData)) headers['Content-Type'] = 'application/json'
    const resp = await fetch(`${this.baseUrl}${path}`, { ...init, headers: { ...headers, ...init?.headers as Record<string, string> } })
    if (!resp.ok) throw new ApiError(resp.status, await resp.json().catch(() => ({})))
    return resp.status === 204 ? (undefined as T) : await resp.json()
  }

  login(username: string, password: string): Promise<TokenResponse> {
    return this.req<TokenResponse>('/api/user/login', {
      method: 'POST',
      body: JSON.stringify({ username, password, client_version: 'client-3d' })
    })
  }

  getWsTicket(): Promise<TokenResponse> {
    return this.req<TokenResponse>('/api/user/ws-ticket', { method: 'POST' })
  }

  getPersona(): Promise<PersonaData> {
    return this.req<PersonaData>('/api/companion/persona')
  }

  getModel(): Promise<ModelData | null> {
    return this.req<ModelData | null>('/api/companion/model')
  }

  generateModel(): Promise<ModelData> {
    return this.req<ModelData>('/api/companion/model', { method: 'POST', body: JSON.stringify({}) })
  }

  getWardrobe(): Promise<WardrobeItem[]> {
    return this.req<WardrobeItem[]>('/api/companion/wardrobe')
  }

  getEquippedOutfit(): Promise<WardrobeItem | null> {
    return this.req<WardrobeItem | null>('/api/companion/wardrobe/equipped')
  }

  equipOutfit(itemId: number): Promise<WardrobeItem> {
    return this.req<WardrobeItem>('/api/companion/wardrobe/equip', {
      method: 'PUT',
      body: JSON.stringify({ item_id: itemId })
    })
  }

  generateOutfit(name: string, description: string): Promise<WardrobeItem> {
    return this.req<WardrobeItem>('/api/companion/wardrobe/generate', {
      method: 'POST',
      body: JSON.stringify({ name, description })
    })
  }
}
