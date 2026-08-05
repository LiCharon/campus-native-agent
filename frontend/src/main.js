import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/dist/index.css'

import App from './App.vue'
import router from './router'
import './styles/main.css'

const app = createApp(App)

// 全量引入 Element Plus（本项目为演示服务台，未做按需加载以保持简单）
app.use(ElementPlus, { locale: zhCn })
app.use(router)

app.mount('#app')
