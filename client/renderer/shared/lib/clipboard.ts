export function installClipboardShim(): void {
  // Routes navigator.clipboard.writeText through Electron IPC
  const ipc = window.spiritagent?.writeClipboard

  if (!ipc || !navigator.clipboard) {
    return
  }

  const native = navigator.clipboard.writeText?.bind(navigator.clipboard)

  const writeText = async (text: string): Promise<void> => {
    try {
      await ipc(text)
    } catch {
      await native?.(text)
    }
  }

  try {
    Object.defineProperty(navigator.clipboard, 'writeText', { configurable: true, value: writeText, writable: true })
  } catch {
    // Browser refused override
  }
}
