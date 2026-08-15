import { defineConfig } from 'tsup'

export default defineConfig({
  entry: {
    entry: 'main/entry.ts',
    preload: 'main/preload.ts',
    'security/paths': 'main/security/paths.ts'
  },
  outDir: 'dist-electron',
  format: ['cjs'],
  target: 'node24',
  platform: 'node',
  external: ['electron', 'electron-log', 'electron-log/main', 'electron-updater', 'ws', 'yaml'],
  clean: true,
  sourcemap: true,
  bundle: true,
  splitting: false,
  dts: false,
  shims: false
})
