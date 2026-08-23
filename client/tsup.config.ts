import { defineConfig } from 'tsup'

// tsup 的 config.watch 只要 truthy 就进 watch 模式——即使 `tsup`（无 --watch）
// 单次构建也会被它劫持成永不退出。要把 watch 范围收窄到 main/shared 同时
// 保留单次构建语义，只能在 config 里检测 process.argv。
const IS_WATCH = process.argv.includes('--watch')

// preload 走 CJS（沙盒不识别 ESM，理由见 README §4）。
const baseOptions = {
  bundle: true,
  dts: false,
  external: ['electron', 'electron-log', 'electron-log/main', 'electron-updater', 'ws', 'yaml'],
  ignoreWatch: [
    '**/dist/**',
    '**/dist-electron/**',
    '**/renderer/**',
    '**/node_modules/**',
    '**/.turbo/**',
    '**/.git/**'
  ],
  // watch 模式收窄到 main/ + shared/（IPC 契约在 shared/ipc/）。
  // 默认 tsup 从 `.` 起步走 tinyglobby 列举全树，再叠加 ignoreWatch 过滤；
  // chokidar 在 Windows 上每收到事件都跑 globSync 评估 ignore 列表，
  // 整体 chokidar + esbuild 增量图持续占 ~40% CPU。把 watch 显式钉到这两个目录后，
  // chokidar 只挂载需要的 fs.watch 句柄，per-event 评估成本随之消失。
  ...(IS_WATCH ? { watch: ['main', 'shared'] } : {}),
  platform: 'node' as const,
  shims: false,
  sourcemap: true,
  splitting: false,
  target: 'node24',
  // esbuild 没有 `resolve.alias` 选项(Vite/Rollup 才有),通过 esbuildOptions
  // 钩子注入别名,使 main/preload 中 `import { IPC } from '@ipc/contracts'` 能
  // 被 esbuild 在打包时解析到 `./shared/ipc/contracts`。
  esbuildOptions(options) {
    options.alias = {
      ...options.alias,
      '@ipc/contracts': './shared/ipc/contracts'
    }
  }
}

export default defineConfig([
  {
    ...baseOptions,
    entry: {
      entry: 'main/entry.ts',
      'security/paths': 'main/security/paths.ts'
    },
    outDir: 'dist-electron',
    format: ['esm'],
    outExtension: () => ({ js: '.js' }),
    clean: false
  },
  {
    ...baseOptions,
    entry: {
      preload: 'main/preload.ts'
    },
    outDir: 'dist-electron',
    format: ['cjs'],
    outExtension: () => ({ js: '.cjs' }),
    clean: false
  }
])
