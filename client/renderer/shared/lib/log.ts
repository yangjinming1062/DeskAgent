// Renderer → main structured logger. Forwards to the `spiritagent:log:emit` IPC
// (main/ipc/log.cjs) so warn/error/info lines land in the desktop log file
// instead of disappearing into DevTools. No-ops when the preload bridge hasn't
// exposed `window.spiritagent.log` yet (early boot / test harness).

type LogLevel = 'error' | 'info' | 'warn'

function emit(level: LogLevel, scope: string, args: unknown[]): void {
  window.spiritagent?.log?.({ level, scope, args })
}

export const log = {
  warn: (scope: string, ...args: unknown[]): void => emit('warn', scope, args),
  error: (scope: string, ...args: unknown[]): void => emit('error', scope, args),
  info: (scope: string, ...args: unknown[]): void => emit('info', scope, args)
} as const
