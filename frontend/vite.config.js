import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发态：/api 代理到本地后端（uvicorn 默认 8000）
// 生产态：npm run build 产物在 dist/，由静态服务器部署，/api 由网关/反代转发
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true
      }
    }
  },
  build: {
    outDir: 'dist'
  }
})
