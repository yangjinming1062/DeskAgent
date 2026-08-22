import { defineConfig } from 'tsup'

export default defineConfig({
  entry: {
    entry: 'main/entry.ts',
    preload: 'main/preload.ts',
    'security/paths': 'main/security/paths.ts'
  },
  outDir: 'dist-electron',
  format: ['esm'],
  target: 'node24',
  platform: 'node',
  outExtension: () => ({ js: '.js' }),
  external: ['electron', 'electron-log', 'electron-log/main', 'electron-updater', 'ws', 'yaml'],
  clean: true,
  sourcemap: true,
  bundle: true,
  splitting: false,
  dts: false,
  shims: false,
  // esbuild 没有 `resolve.alias` 选项(Vite/Rollup 才有),通过 esbuildOptions
  // 钩子注入别名,使 main/preload 中 `import { IPC } from '@ipc/contracts'` 能
  // 被 esbuild 在打包时解析到 `./shared/ipc/contracts`。
  esbuildOptions(options) {
    options.alias = {
      ...options.alias,
      '@ipc/contracts': './shared/ipc/contracts'
    }
  }
})
