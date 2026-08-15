import type { IpcMain } from 'electron'

import * as store from '../shared/lib/runner-config-store'

export function registerRunnerConfigIpc({ ipcMain }: { ipcMain: IpcMain }): void {
  ipcMain.handle('deskagent:runner-config:read', async () => {
    try {
      const content = JSON.stringify(store.read(), null, 2)

      return { content, ok: true }
    } catch (error: any) {
      return { error: error.message, ok: false }
    }
  })

  ipcMain.handle('deskagent:runner-config:write', async (_event, newContent) => {
    if (typeof newContent !== 'string') {
      return { error: 'config content must be a string', ok: false }
    }

    let obj: any

    try {
      obj = JSON.parse(newContent)
    } catch (error: any) {
      return { error: error.message, ok: false }
    }

    if (!obj || typeof obj !== 'object' || Array.isArray(obj)) {
      return { error: 'config root must be a JSON object', ok: false }
    }

    return store.write(obj)
  })

  ipcMain.handle('deskagent:runner-config:patch', async (_event, patch) => {
    if (!patch || !Array.isArray(patch.path) || patch.path.length === 0) {
      return { error: 'patch.path must be a non-empty array', ok: false }
    }

    const op = patch.op ?? 'set'

    if (op !== 'set' && op !== 'delete') {
      return { error: `unknown op: ${op}`, ok: false }
    }

    return store.patch(patch.path, { op, value: patch.value })
  })
}
