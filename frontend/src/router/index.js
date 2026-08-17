import { createRouter, createWebHistory } from 'vue-router'

import Login from '../views/Login.vue'
import Chat from '../views/Chat.vue'
import CsWorkbench from '../views/CsWorkbench.vue'
import AdminReview from '../views/AdminReview.vue'
import StatsDashboard from '../views/StatsDashboard.vue'
import UserManage from '../views/UserManage.vue'
import LogViewer from '../views/LogViewer.vue'
import { PERM, effectivePerms, currentUser } from '../constants/perms'
import { BRAND } from '../config/brand'

// 登录后按角色进入的首页（与 docs/ui/Login-Page-Design.md §9 一致）
export const ROLE_HOME = {
  student: '/chat',
  cs_staff: '/cs',
  admin: '/stats'
}

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/chat' },
    { path: '/login', component: Login, meta: { title: '登录' } },
    { path: '/chat', component: Chat, meta: { title: '对话提交' } },
    { path: '/cs', component: CsWorkbench, meta: { title: '客服工作台', perm: PERM.CS_WORKBENCH } },
    { path: '/admin', component: AdminReview, meta: { title: '知识库管理', perm: PERM.KB_REVIEW } },
    { path: '/stats', component: StatsDashboard, meta: { title: '数据看板', perm: PERM.VIEW_STATS } },
    { path: '/users', component: UserManage, meta: { title: '用户管理', perm: PERM.USER_MGMT } },
    { path: '/logs', component: LogViewer, meta: { title: '日志管理', perm: PERM.VIEW_LOGS } },
    { path: '/:pathMatch(.*)*', redirect: '/chat' }
  ]
})

// 全局守卫：未登录回登录页；已登录访问登录页回对话页；权限位不够回对话页
router.beforeEach((to) => {
  const token = localStorage.getItem('cd_token')

  if (to.path !== '/login' && !token) {
    return { path: '/login' }
  }
  if (to.path === '/login' && token) {
    const u = currentUser()
    return { path: ROLE_HOME[u.role] || '/chat' }
  }
  if (to.meta.perm) {
    const user = currentUser()
    const perms = effectivePerms(user.role, user.permissions)
    if (!perms.includes(to.meta.perm)) {
      return { path: '/chat' }
    }
  }
  return true
})

router.afterEach((to) => {
  document.title = to.meta.title ? `${to.meta.title} · ${BRAND.titleSuffix}` : BRAND.titleSuffix
})

export default router
