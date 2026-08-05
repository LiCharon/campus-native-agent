import { createRouter, createWebHistory } from 'vue-router'

import Login from '../views/Login.vue'
import Chat from '../views/Chat.vue'
import MyTickets from '../views/MyTickets.vue'
import Management from '../views/Management.vue'
import Dashboard from '../views/Dashboard.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/chat' },
    { path: '/login', component: Login, meta: { title: '登录' } },
    { path: '/chat', component: Chat, meta: { title: '对话提交' } },
    { path: '/tickets', component: MyTickets, meta: { title: '我的工单' } },
    {
      path: '/management',
      component: Management,
      meta: { title: '管理列表', roles: ['staff', 'it_staff', 'admin'] }
    },
    {
      path: '/dashboard',
      component: Dashboard,
      meta: { title: '数据看板', roles: ['staff', 'it_staff', 'admin'] }
    },
    { path: '/:pathMatch(.*)*', redirect: '/chat' }
  ]
})

// 全局守卫：未登录一律回登录页；管理列表/看板校验角色，越权回对话页
router.beforeEach((to) => {
  const token = localStorage.getItem('cd_token')

  if (to.path !== '/login' && !token) {
    return { path: '/login' }
  }
  if (to.path === '/login' && token) {
    return { path: '/chat' }
  }

  if (to.meta && to.meta.roles) {
    const raw = localStorage.getItem('cd_user')
    const user = raw ? JSON.parse(raw) : {}
    if (!to.meta.roles.includes(user.role)) {
      return { path: '/chat' }
    }
  }
  return true
})

router.afterEach((to) => {
  document.title = to.meta.title ? `${to.meta.title} · CampusDesk 校园服务台` : 'CampusDesk 校园服务台'
})

export default router
