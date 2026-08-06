<template>
  <!-- M7：Element Plus 按需后无全局 app.use(ElementPlus)，locale 改由 el-config-provider 注入 -->
  <el-config-provider :locale="zhCn">
  <!-- 登录页：无骨架布局 -->
  <router-view v-if="isLoginPage" />

  <el-container v-else class="layout">
    <el-header class="header">
      <div class="brand">
        <el-icon :size="22" color="#409eff"><Service /></el-icon>
        <span class="brand-title">CampusDesk 校园服务台</span>
      </div>
      <div class="user-area">
        <el-tag size="small" :type="roleTagType">{{ roleName }}</el-tag>
        <span class="username">{{ displayName }}</span>
        <el-button link type="primary" @click="handleLogout">退出登录</el-button>
      </div>
    </el-header>

    <el-container class="body">
      <el-aside width="200px" class="aside">
        <el-menu :default-active="activeMenu" router class="menu">
          <el-menu-item index="/chat">
            <el-icon><ChatDotRound /></el-icon>
            <span>对话提交</span>
          </el-menu-item>
          <el-menu-item index="/tickets">
            <el-icon><Tickets /></el-icon>
            <span>我的工单</span>
          </el-menu-item>
          <template v-if="isStaffOrAbove">
            <el-menu-item index="/management">
              <el-icon><List /></el-icon>
              <span>管理列表</span>
            </el-menu-item>
            <el-menu-item index="/dashboard">
              <el-icon><DataAnalysis /></el-icon>
              <span>数据看板</span>
            </el-menu-item>
          </template>
        </el-menu>
      </el-aside>

      <el-main class="main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
  </el-config-provider>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import {
  Service,
  ChatDotRound,
  Tickets,
  List,
  DataAnalysis
} from '@element-plus/icons-vue'
import { ROLE_CN } from './constants/status'
import { logout } from './api/auth'

const route = useRoute()
const router = useRouter()

const isLoginPage = computed(() => route.path === '/login')

// 注意：computed 必须有响应式依赖——localStorage 不是响应式的，
// 不加依赖会缓存首次挂载时的旧值（登录跳转后仍显示"未知/用户"，
// 刷新才正常，验收抓出）。route.path 变化（登录/登出跳转）时强制重算。
const user = computed(() => {
  void route.path
  try {
    return JSON.parse(localStorage.getItem('cd_user') || '{}')
  } catch {
    return {}
  }
})

const roleName = computed(() => ROLE_CN[user.value.role] || user.value.role || '未知')
const displayName = computed(() => user.value.name || user.value.username || '用户')
const isStaffOrAbove = computed(() =>
  ['staff', 'it_staff', 'admin'].includes(user.value.role)
)

const roleTagType = computed(() => {
  const map = { student: 'info', staff: 'warning', it_staff: 'warning', admin: 'danger' }
  return map[user.value.role] || 'info'
})

const activeMenu = computed(() => route.path)

function handleLogout() {
  logout()
  router.push('/login')
}
</script>

<style scoped>
.layout {
  height: 100%;
}

.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #fff;
  border-bottom: 1px solid #e4e7ed;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.04);
}

.brand {
  display: flex;
  align-items: center;
  gap: 8px;
}

.brand-title {
  font-size: 17px;
  font-weight: 600;
  color: #303133;
}

.user-area {
  display: flex;
  align-items: center;
  gap: 10px;
}

.username {
  font-size: 14px;
  color: #303133;
}

.body {
  height: calc(100% - 60px);
}

.aside {
  background: #fff;
  border-right: 1px solid #e4e7ed;
}

.menu {
  border-right: none;
}

.main {
  overflow-y: auto;
}
</style>
