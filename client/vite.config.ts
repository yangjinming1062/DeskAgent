import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

export default defineConfig({
  base: './',
  plugins: [react(), tailwindcss()],
  // icon.png 的唯一真相源——main.cjs、electron-builder 和渲染器共用同一文件
  publicDir: 'assets',
  css: {
    // 显式钉死空 PostCSS 配置：Tailwind 由 @tailwindcss/vite 处理，无需 PostCSS 插件。
    // 不钉的话 Vite 会向上查找 postcss.config.*，用户目录里可能有 Tailwind v3 配置
    // 导致 v4 样式表构建失败（"@layer base is used but no matching @tailwind base"）。
    postcss: { plugins: [] }
  },
  build: {
    chunkSizeWarningLimit: 1000
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './renderer'),
      '@shared': path.resolve(__dirname, './renderer/shared'),
      '@companion': path.resolve(__dirname, './renderer/companion'),
      '@hub': path.resolve(__dirname, './renderer/hub'),
      '@ipc/contracts': path.resolve(__dirname, './shared/ipc/contracts')
    },
    dedupe: ['react', 'react-dom']
  },
  server: {
    host: '127.0.0.1',
    port: 5174,
    strictPort: true
  },
  preview: {
    host: '127.0.0.1',
    port: 4174
  },
  // Vitest 负责渲染进程测试；主进程测试用 node --test（见 package.json）
  test: {
    // 仅运行时测试。.test-d.ts 必须放在 typecheck.include 里,否则 Vitest
    // 尝试执行无 it()/test() 的文件会报 "No test suite found"。
    include: ['renderer/**/*.{test,spec}.{ts,tsx}'],
    environment: 'jsdom',
    typecheck: {
      enabled: true,
      include: ['shared/**/*.test-d.ts'],
      tsconfig: './tsconfig.json'
    }
  }
})
