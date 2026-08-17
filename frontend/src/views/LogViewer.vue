<template>
  <div class="page">
    <div class="cd-ph">
      <div>
        <h2>日志管理</h2>
        <p>审计日志（操作人 / 动作 / 对象 / 时间）</p>
      </div>
      <button class="cd-btn ghost" @click="load">
        <svg viewBox="0 0 24 24" class="ic"><path d="M4 4v6h6M20 20v-6h-6"/><path d="M4 10a8 8 0 0 1 14-3M20 14a8 8 0 0 1-14 3"/></svg>
        刷新
      </button>
    </div>
    <div class="content">
      <div class="cd-filters">
        <select v-model="filterUser">
          <option value="">全部操作人</option>
          <option v-for="u in users" :key="u" :value="u">{{ u }}</option>
        </select>
        <select v-model="filterAction">
          <option value="">全部动作</option>
          <option v-for="a in actions" :key="a" :value="a">{{ actionLabel(a) }}</option>
        </select>
      </div>
      <div class="cd-table-card">
        <table>
          <thead>
            <tr><th>操作人</th><th>动作</th><th>对象</th><th>详情</th><th>时间</th></tr>
          </thead>
          <tbody>
            <tr v-for="row in filtered" :key="row.id">
              <td class="mono">{{ row.user_id }}</td>
              <td>{{ actionLabel(row.action) }}</td>
              <td class="mono">{{ row.object_type }}{{ row.object_id ? ` · #${row.object_id}` : '' }}</td>
              <td class="detail">{{ row.detail || '—' }}</td>
              <td class="mono">{{ formatTime(row.created_at) }}</td>
            </tr>
            <tr v-if="!filtered.length"><td colspan="5"><div class="cd-empty">暂无日志</div></td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { fetchLogs } from '../api/admin'

const ACTION_LABEL = {
  login: '登录成功',
  adopt: '采纳知识',
  dismiss: '驳回',
  cs_resolve: '标记已处理',
  user_create: '新增用户',
  user_update: '编辑用户',
  user_reset_password: '重置密码'
}

const all = ref([])
const filterUser = ref('')
const filterAction = ref('')

const users = computed(() => [...new Set(all.value.map((l) => l.user_id))].sort())
const actions = computed(() => [...new Set(all.value.map((l) => l.action))].sort())
const filtered = computed(() =>
  all.value.filter(
    (l) =>
      (!filterUser.value || l.user_id === filterUser.value) &&
      (!filterAction.value || l.action === filterAction.value)
  )
)

function actionLabel(a) {
  return ACTION_LABEL[a] || a
}

async function load() {
  try {
    const params = {}
    if (filterUser.value) params.user_id = filterUser.value
    if (filterAction.value) params.action = filterAction.value
    const resp = await fetchLogs(params)
    all.value = resp.data.items || []
  } catch {
    ElMessage.error('加载失败，请检查后端是否启动')
    all.value = []
  }
}

function formatTime(ts) {
  if (!ts) return ''
  return new Date(ts).toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'
  })
}

onMounted(load)
</script>

<style scoped>
.page {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
  background: var(--cd-bg);
}

.content {
  padding: 24px 28px;
  overflow-y: auto;
  flex: 1;
}

.mono {
  font-family: 'SF Mono', 'Cascadia Code', Consolas, monospace;
  color: var(--cd-text-2);
  font-size: 12.5px;
}

.detail {
  color: var(--cd-text-2);
  font-size: 13px;
  max-width: 300px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.cd-filters select {
  font-family: inherit;
  font-size: 13px;
  padding: 9px 12px;
  border: 1px solid var(--cd-line);
  border-radius: var(--cd-radius-btn);
  background: #fff;
  color: var(--cd-text-1);
}

.ic {
  width: 14px;
  height: 14px;
  stroke: currentColor;
  stroke-width: 1.8;
  fill: none;
  stroke-linecap: round;
  stroke-linejoin: round;
}
</style>
