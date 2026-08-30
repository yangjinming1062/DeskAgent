import type { SpiritAgentApiRequest } from '@ipc/contracts'

import { $auth } from '@/shared/store/auth'

/** 鉴权 RPC 的统一返回：不把 auth-loss / 网络错 / 后端错 / void body 压成同一个 null，
 * 调用方按 reason 分流——避免 mesh2d 等 store 把"登出 race"误判成"请求成功无返回"而把状态卡在 'generating'。 */
export type AuthedApiResult<T> =
  | { ok: true; value: T | null }
  | { ok: false; reason: 'unauth' }
  | { ok: false; reason: 'err'; error: unknown }

export async function authedApi<T>(opts: SpiritAgentApiRequest): Promise<AuthedApiResult<T>> {
  if ($auth.get().kind !== 'authenticated') {
    return { ok: false, reason: 'unauth' }
  }

  try {
    const value = (await window.spiritagent.api<T>(opts)) as T | null

    if ($auth.get().kind !== 'authenticated') {
      return { ok: false, reason: 'unauth' }
    }

    return { ok: true, value }
  } catch (error) {
    return { ok: false, error, reason: 'err' }
  }
}
