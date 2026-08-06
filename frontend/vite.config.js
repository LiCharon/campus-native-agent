import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import AutoImport from 'unplugin-auto-import/vite'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'

// 开发态：/api 代理到本地后端（uvicorn 默认 8000）
// 生产态：npm run build 产物在 dist/，由静态服务器部署，/api 由网关/反代转发
export default defineConfig({
  plugins: [
    vue(),
    // M7：Element Plus 按需引入——unplugin-auto-import 处理 script 中未显式 import 的
    // 函数式组件（ElMessage 等），unplugin-vue-components 处理模板组件与指令（v-loading）
    // 及对应样式（importStyle 默认 'css'），产物 gzip 由 ~768KB 降至 <450KB
    AutoImport({
      imports: ['vue', 'vue-router'],
      resolvers: [ElementPlusResolver()],
      dts: false
    }),
    Components({
      resolvers: [ElementPlusResolver()],
      dts: false
    })
  ],
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
