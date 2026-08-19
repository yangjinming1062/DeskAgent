// 渲染进程 → 主进程的结构化日志器。转发到 `spiritagent:log:emit` IPC
//（main/ipc/log.cjs），让 warn / error / info 行落到桌面日志文件，
// 而不是淹没在 DevTools 里。若 preload 桥尚未暴露 `window.spiritagent.log`
// （早期启动 / 测试环境）则为空操作。

type LogLevel = 'error' | 'info' | 'warn'

function emit(level: LogLevel, scope: string, args: unknown[]): void {
  window.spiritagent?.log?.({ level, scope, args })
}

export const log = {
  warn: (scope: string, ...args: unknown[]): void => emit('warn', scope, args),
  error: (scope: string, ...args: unknown[]): void => emit('error', scope, args),
  info: (scope: string, ...args: unknown[]): void => emit('info', scope, args)
} as const
