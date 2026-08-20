<template>
  <!-- M4：Kimi 布局壳（设计 v3 §3.3）——品牌 → ＋新对话 → 工作台菜单 → 会话列表 → 用户卡 -->
  <el-config-provider :locale="zhCn">
    <router-view v-if="isLoginPage" />

    <div v-else class="shell">
      <aside class="sidebar">
        <!-- 品牌 -->
        <div class="brand" @click="go('/chat')">
          <span class="logo">
            <svg viewBox="0 0 24 24" class="logo-svg"><path d="M3 10 12 4l9 6"/><path d="M5 9v10h14V9"/><path d="M9 19v-6h6v6"/><path d="M3 19h18"/></svg>
          </span>
          <span class="wm">{{ BRAND.name }}<small>{{ BRAND.subtitle }}</small></span>
        </div>

        <!-- ＋新对话 -->
        <button class="new-chat" @click="handleNewConversation">
          <svg viewBox="0 0 24 24" class="ic"><path d="M12 5v14M5 12h14"/></svg>
          新对话
        </button>

        <!-- 工作台菜单（权限过滤） -->
        <nav v-if="menus.length" class="nav-section">
          <div class="section-title">工作台</div>
          <div
            v-for="m in menus"
            :key="m.path"
            class="menu-item"
            :class="{ active: route.path.startsWith(m.path) }"
            @click="go(m.path)"
          >
            <svg class="ic" viewBox="0 0 24 24" v-html="iconPath(m.icon)"></svg>
            <span>{{ m.title }}</span>
            <span v-if="badge(m.path)" class="badge">{{ badge(m.path) }}</span>
          </div>
        </nav>

        <div class="divider" />

        <!-- 会话列表（纯文字，hover 重命名） -->
        <div class="nav-section conv-sec">
          <div class="section-title">会话</div>
          <div class="conv-list">
            <div
              v-for="conv in conversations"
              :key="conv.id"
              class="conv-item"
              :class="{ active: conv.id === currentId }"
              @click="handleSwitch(conv.id)"
              @dblclick="startRename(conv)"
            >
              <template v-if="renamingId === conv.id">
                <input
                  v-model="renameDraft"
                  class="rename-input"
                  @click.stop
                  @keydown.enter="commitRename"
                  @keydown.esc="cancelRename"
                  @blur="commitRename"
                />
              </template>
              <template v-else>
                <span class="conv-title">{{ conv.title }}</span>
                <el-icon class="rename-icon" title="重命名" @click.stop="startRename(conv)">
                  <EditPen />
                </el-icon>
              </template>
            </div>
            <div v-if="conversations.length === 0" class="conv-empty">暂无会话</div>
          </div>
        </div>

        <!-- 用户卡 -->
        <div class="user-card">
          <div class="avatar">{{ displayName.slice(0, 1) }}</div>
          <div class="meta">
            <div class="nm">{{ displayName }}</div>
            <div class="rl">{{ roleName }}</div>
          </div>
          <span class="out" title="退出登录" @click="handleLogout">
            <svg viewBox="0 0 24 24" class="ic"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="M16 17l5-5-5-5M21 12H9"/></svg>
          </span>
        </div>
      </aside>

      <main class="main">
        <router-view />
      </main>
    </div>
  </el-config-provider>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import { EditPen } from '@element-plus/icons-vue'
import { ROLE_CN } from './constants/status'
import { PERM_MENUS, effectivePerms, currentUser } from './constants/perms'
import { logout } from './api/auth'
import { useChat } from './composables/useChat'
import { BRAND } from './config/brand'

// 工作台菜单 SVG 图标 path（线性，与 main.css .ic 统一）
const ICON_PATHS = {
  Headset: '<path d="M4 13a8 8 0 0 1 16 0"/><rect x="3" y="13" width="4" height="7" rx="2"/><rect x="17" y="13" width="4" height="7" rx="2"/>',
  Collection: '<path d="M4 5h7v15H4zM13 5h7v15h-7z"/>',
  DataLine: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
  UserFilled: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c.8-3.5 3.4-5.5 6.5-5.5s5.7 2 6.5 5.5"/><circle cx="17.5" cy="9" r="2.5"/><path d="M15.5 14.5c2.8-.4 5 1.2 5.9 4.4"/>',
  Document: '<path d="M6 3h9l4 4v14H6z"/><path d="M15 3v4h4"/><path d="M9 12h6M9 16h6"/>'
}

const route = useRoute()
const router = useRouter()

const isLoginPage = computed(() => route.path === '/login')

// localStorage 非响应式：route.path 变化时重算
const user = computed(() => {
  void route.path
  return currentUser()
})

const roleName = computed(() => ROLE_CN[user.value.role] || user.value.role || '未知')
const displayName = computed(() => user.value.name || user.value.username || '用户')

const menus = computed(() => {
  const perms = effectivePerms(user.value.role, user.value.permissions)
  return PERM_MENUS.filter((m) => perms.includes(m.perm))
})

const {
  conversations,
  currentId,
  newConversation,
  switchConversation,
  deleteConversation,
  clearConversations,
  renameConversation,
  reload
} = useChat()

void reload() // M5-ZJUT：异步拉取会话列表（服务端）

// 会话用户隔离（bug 修复）：模块级会话状态只 load 一次，换账号（user.id 变）强制重载
watch(
  () => user.value.id,
  () => void reload()
)

// ---- 会话重命名（Kimi §5.2：双击/hover 行内编辑） ----
const renamingId = ref(null)
const renameDraft = ref('')

function startRename(conv) {
  renamingId.value = conv.id
  renameDraft.value = conv.title
}
function commitRename() {
  if (renamingId.value !== null) {
    renameConversation(renamingId.value, renameDraft.value)
    renamingId.value = null
  }
}
function cancelRename() {
  renamingId.value = null
}

async function handleNewConversation() {
  await newConversation() // M5-ZJUT：服务端建会话
  go('/chat')
}

async function handleSwitch(id) {
  await switchConversation(id) // 切换会话 + 拉取消息历史
  go('/chat')
}

function handleLogout() {
  clearConversations() // 用户隔离：登出清空内存会话态，防残留上一账号
  logout()
  router.push('/login')
}

function go(path) {
  if (route.path !== path) router.push(path)
}

function iconPath(name) {
  return ICON_PATHS[name] || ''
}

function badge(path) {
  void path
  return ''
}

// 403 权限提示（对抗性审查 #5）：改权限/禁用后旧 token 失效 → 提示重新登录
onMounted(() => {
  window.addEventListener('cd-perm-denied', (e) => {
    ElMessage.error(e.detail || '无权访问')
  })
})
</script>

<style scoped>
.shell {
  display: flex;
  height: 100%;
}

/* ---------- 侧栏（Kimi 272px） ---------- */
.sidebar {
  width: 272px;
  flex-shrink: 0;
  background: var(--cd-card);
  border-right: 1px solid var(--cd-line);
  display: flex;
  flex-direction: column;
  padding: 18px 14px;
}

.brand {
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 2px 6px 16px;
  cursor: pointer;
}

.brand .logo {
  width: 36px;
  height: 36px;
  border-radius: 11px;
  background: var(--cd-grad);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 4px 12px rgba(20, 84, 156, 0.28);
  flex-shrink: 0;
}

.logo-svg {
  width: 19px;
  height: 19px;
  stroke: #fff;
  stroke-width: 1.7;
  fill: none;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.brand .wm {
  font-size: 16px;
  font-weight: 700;
  letter-spacing: 0.2px;
  line-height: 1.15;
  color: var(--cd-text-1);
}

.brand .wm small {
  display: block;
  font-size: 11px;
  font-weight: 500;
  color: var(--cd-text-3);
  margin-top: 2px;
}

.new-chat {
  display: flex;
  flex-direction: row;         /* 锁定横排，防 flex 坍塌成列 */
  align-items: center;
  justify-content: flex-start;
  gap: 8px;
  width: 100%;                /* 拉满侧栏内容区，左右边框对齐 */
  white-space: nowrap;
  padding: 9px 14px;
  margin: 0 0 12px;
  border: 1px solid var(--cd-line);
  border-radius: 12px;
  background: #fff;
  color: var(--cd-primary);
  font-size: 13px;
  font-weight: 600;
  font-family: inherit;
  cursor: pointer;
  transition: border-color 0.14s, background 0.14s;
}

.new-chat .ic {
  width: 14px;
  height: 14px;
  stroke: currentColor;
  stroke-width: 1.8;
  fill: none;
  stroke-linecap: round;
  stroke-linejoin: round;
  flex-shrink: 0;
}

.new-chat:hover {
  border-color: var(--cd-primary);
  background: var(--cd-primary-soft);
}

.nav-section {
  display: flex;
  flex-direction: column;
}

.section-title {
  font-size: 11px;
  font-weight: 600;
  color: var(--cd-text-3);
  letter-spacing: 0.8px;
  text-transform: uppercase;
  padding: 6px 10px 8px;
}

.menu-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 11px;
  border-radius: 10px;
  font-size: 13.5px;
  color: var(--cd-text-2);
  cursor: pointer;
  margin-bottom: 2px;
  transition: background 0.14s, color 0.14s;
  border: 1px solid transparent;
}

.menu-item:hover {
  background: var(--cd-panel);
  color: var(--cd-text-1);
}

.menu-item.active {
  background: var(--cd-primary-soft);
  color: var(--cd-primary);
  font-weight: 600;
  border-color: #d6e4f7;
}

.menu-item .ic {
  width: 15px;
  height: 15px;
  stroke: currentColor;
  stroke-width: 1.7;
  fill: none;
  stroke-linecap: round;
  stroke-linejoin: round;
  flex-shrink: 0;
}

.menu-item .badge {
  margin-left: auto;
  font-size: 10.5px;
  font-weight: 700;
  background: var(--cd-primary-soft);
  color: var(--cd-primary);
  border-radius: 99px;
  padding: 1px 8px;
}

.divider {
  height: 1px;
  background: var(--cd-line);
  margin: 12px 6px;
}

/* 会话列表 */
.conv-sec {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.conv-list {
  overflow-y: auto;
  flex: 1;
}

.conv-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 9px 11px;
  border-radius: 10px;
  font-size: 13px;
  color: var(--cd-text-2);
  cursor: pointer;
  margin-bottom: 2px;
  transition: background 0.14s;
  border: 1px solid transparent;
  min-height: 34px;
}

.conv-item:hover {
  background: var(--cd-panel);
  color: var(--cd-text-1);
}

.conv-item.active {
  background: var(--cd-primary-soft);
  color: var(--cd-primary);
  font-weight: 600;
  border-color: #d6e4f7;
}

.conv-title {
  flex: 1;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.rename-icon {
  color: var(--cd-text-3);
  visibility: hidden;
  font-size: 13px;
  flex-shrink: 0;
}

.conv-item:hover .rename-icon {
  visibility: visible;
}

.rename-icon:hover {
  color: var(--cd-primary);
}

.rename-input {
  flex: 1;
  min-width: 0;
  border: 1px solid var(--cd-primary);
  border-radius: 6px;
  padding: 3px 6px;
  font-size: 13px;
  font-family: inherit;
  outline: none;
  background: #fff;
}

.conv-empty {
  padding: 14px 10px;
  font-size: 12.5px;
  color: var(--cd-text-3);
  text-align: center;
}

/* 用户卡 */
.user-card {
  margin-top: auto;
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 11px 8px;
  border-top: 1px solid var(--cd-line);
}

.avatar {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  font-weight: 700;
  color: #fff;
  background: var(--cd-grad);
}

.user-card .meta {
  line-height: 1.35;
  flex: 1;
  min-width: 0;
}

.user-card .meta .nm {
  font-size: 13.5px;
  font-weight: 600;
  color: var(--cd-text-1);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.user-card .meta .rl {
  font-size: 11.5px;
  color: var(--cd-text-3);
}

.user-card .out {
  color: var(--cd-text-3);
  cursor: pointer;
  display: flex;
  padding: 4px;
}

.user-card .out:hover {
  color: var(--cd-text-1);
}

.user-card .ic {
  width: 17px;
  height: 17px;
  stroke: currentColor;
  stroke-width: 1.7;
  fill: none;
  stroke-linecap: round;
  stroke-linejoin: round;
}

/* 主区 */
.main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: var(--cd-bg);
  overflow: hidden;
}
</style>
