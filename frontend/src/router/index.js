import { createRouter, createWebHistory } from 'vue-router'

import Login from '../views/Login.vue'
import Chat from '../views/Chat.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/chat' },
    { path: '/login', component: Login, meta: { title: '登录' } },
    { path: '/chat', component: Chat, meta: { title: '对话提交' } },
    { path: '/:pathMatch(.*)*', redirect: '/chat' }
  ]
})

// 全局守卫：未登录一律回登录页；已登录访问登录页回对话页
router.beforeEach((to) => {
  const token = localStorage.getItem('cd_token')

  if (to.path !== '/login' && !token) {
    return { path: '/login' }
  }
  if (to.path === '/login' && token) {
    return { path: '/chat' }
  }
  return true
})

router.afterEach((to) => {
  document.title = to.meta.title ? `${to.meta.title} · CampusDesk 校园服务台` : 'CampusDesk 校园服务台'
})

export default router
