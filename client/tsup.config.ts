import { defineConfig } from 'tsup'

// preload 走 CJS（沙盒不识别 ESM，理由见 README §4）。
const baseOptions = {
  bundle: true,
  dts: false,
  external: ['electron', 'electron-log', 'electron-log/main', 'electron-updater', 'ws', 'yaml'],
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
    clean: true
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