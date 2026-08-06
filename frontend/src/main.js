import { createApp } from 'vue'
// M7：Element Plus 已按需引入——模板组件/指令（含 v-loading）及样式由
// unplugin-vue-components + unplugin-auto-import 自动注入，不再全量
// app.use(ElementPlus) + index.css。ElMessage/ElMessageBox 是函数式组件，
// 插件不处理其样式，需在此显式引入；locale 由 App.vue 的 el-config-provider 注入。
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/message-box/style/css'

import App from './App.vue'
import router from './router'
import './styles/main.css'

const app = createApp(App)

app.use(router)

app.mount('#app')
