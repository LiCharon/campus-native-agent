import { createRouter, createWebHistory } from 'vue-router'

import Login from '../views/Login.vue'
import Chat from '../views/Chat.vue'
import AdminReview from '../views/AdminReview.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/chat' },
    { path: '/login', component: Login, meta: { title: '登录' } },
    { path: '/chat', component: Chat, meta: { title: '对话提交' } },
    { path: '/admin', component: AdminReview, meta: { title: '知识库管理', requiresAdmin: true } },
    { path: '/:pathMatch(.*)*', redirect: '/chat' }
  ]
})

// 全局守卫：未登录一律回登录页；已登录访问登录页回对话页；admin 专属页验角色
router.beforeEach((to) => {
  const token = localStorage.getItem('cd_token')

  if (to.path !== '/login' && !token) {
    return { path: '/login' }
  }
  if (to.path === '/login' && token) {
    return { path: '/chat' }
  }
  if (to.meta.requiresAdmin) {
    let role = ''
    try {
      role = JSON.parse(localStorage.getItem('cd_user') || '{}').role || ''
    } catch {
      /* 保持空 */
    }
    if (role !== 'admin') {
      return { path: '/chat' }
    }
  }
  return true
})

router.afterEach((to) => {
  document.title = to.meta.title ? `${to.meta.title} · Campus Native Agent` : 'Campus Native Agent'
})

export default router
