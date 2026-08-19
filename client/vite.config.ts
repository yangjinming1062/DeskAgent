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
    // Shiki 默认产出大量动态 chunk，electron-builder 扫描数千文件会 OOM。
    // 刻意合并为单 chunk（~22 MB），阈值调高以静默 500 kB 警告。
    chunkSizeWarningLimit: 25000,
    rolldownOptions: {
      output: {
        codeSplitting: false
      }
    }
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './renderer'),
      '@shared': path.resolve(__dirname, './renderer/shared'),
      '@companion': path.resolve(__dirname, './renderer/companion'),
      '@hub': path.resolve(__dirname, './renderer/hub')
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
    include: ['renderer/**/*.{test,spec}.{ts,tsx}'],
    environment: 'jsdom'
  }
})
