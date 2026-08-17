<template>
  <div class="page">
    <div class="cd-ph">
      <div>
        <h2>客服工作台</h2>
        <p>转人工的会话在此处理：查看详情 → 线下/电话联系学生 → 标记已处理</p>
      </div>
      <button class="cd-btn ghost" @click="load">
        <svg viewBox="0 0 24 24" class="ic"><path d="M4 4v6h6M20 20v-6h-6"/><path d="M4 10a8 8 0 0 1 14-3M20 14a8 8 0 0 1-14 3"/></svg>
        刷新
      </button>
    </div>
    <div class="content">
      <div class="cd-table-card">
        <table>
          <thead>
            <tr><th style="width:34%">问题</th><th style="width:22%">机器人回复摘要</th><th>备注</th><th>提交人</th><th>时间</th><th style="width:96px">状态</th></tr>
          </thead>
          <tbody>
            <tr v-for="row in items" :key="row.id" class="row-hover" @click="openDrawer(row)">
              <td>{{ row.question }}</td>
              <td class="mono">{{ row.reply || '—（转人工）' }}</td>
              <td class="mono">{{ row.note || '—' }}</td>
              <td class="mono">{{ row.user_id }}</td>
              <td class="mono">{{ formatTime(row.created_at) }}</td>
              <td><span class="cd-tag amber">待处理</span></td>
            </tr>
            <tr v-if="!items.length">
              <td colspan="6"><div class="cd-empty">暂无待接待会话——转人工的会话会出现在这里</div></td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- 详情抽屉（Kimi M4：打包信息） -->
    <div class="overlay" :class="{ open: drawerVisible }" @click.self="drawerVisible = false">
      <div class="drawer">
        <div class="dh">
          <h3>转人工会话 · #{{ currentRow ? currentRow.id : '' }}</h3>
          <span class="x" @click="drawerVisible = false">
            <svg viewBox="0 0 24 24" class="ic"><path d="M18 6 6 18M6 6l12 12"/></svg>
          </span>
        </div>
        <div class="db">
          <div class="field"><label>问题 QUESTION</label><div class="val">{{ currentRow ? currentRow.question : '' }}</div></div>
          <div class="field"><label>AGENT 回复</label><div class="val box">{{ currentRow ? currentRow.reply || '—（转人工）' : '' }}</div></div>
          <div class="field"><label>补充说明 NOTE</label><div class="val box">{{ currentRow ? currentRow.note || '—' : '' }}</div></div>
          <div class="field"><label>来源通道</label><div class="val">{{ currentRow && currentRow.reply ? '对话页反馈' : '转人工自动沉淀' }}</div></div>
          <div class="field"><label>提交人 / 时间</label><div class="val mono">{{ currentRow ? `${currentRow.user_id} · ${formatTime(currentRow.created_at)}` : '' }}</div></div>
          <p class="pack-tip">打包信息：对话摘要 + 已排查步骤 + 初步判断（人机协同，客服无需重复询问）。</p>
        </div>
        <div class="df">
          <button class="cd-btn ghost" @click="drawerVisible = false">关闭</button>
          <button class="cd-btn" :disabled="resolving" @click="handleResolve">
            <svg viewBox="0 0 24 24" class="ic"><path d="M5 12l4 4L19 6"/></svg>
            标记已处理
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { fetchQueue, resolveCase } from '../api/cs'

const items = ref([])
const drawerVisible = ref(false)
const currentRow = ref(null)
const resolving = ref(false)

async function load() {
  try {
    const resp = await fetchQueue()
    items.value = resp.data.items || []
  } catch {
    ElMessage.error('加载失败，请检查后端是否启动')
    items.value = []
  }
}

function openDrawer(row) {
  currentRow.value = row
  drawerVisible.value = true
}

async function handleResolve() {
  if (!currentRow.value) return
  resolving.value = true
  try {
    await resolveCase(currentRow.value.id)
    ElMessage.success('已标记已处理')
    drawerVisible.value = false
    await load()
  } catch {
    ElMessage.error('操作失败，请稍后重试')
  } finally {
    resolving.value = false
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

.row-hover {
  cursor: pointer;
}

.mono {
  font-family: 'SF Mono', 'Cascadia Code', Consolas, monospace;
  color: var(--cd-text-2);
  font-size: 12.5px;
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

.overlay {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.45);
  display: none;
  z-index: 40;
  align-items: flex-end;
  justify-content: flex-end;
}

.overlay.open {
  display: flex;
}

.drawer {
  width: 480px;
  max-width: 94vw;
  height: 100%;
  background: #fff;
  box-shadow: -8px 0 40px rgba(0, 0, 0, 0.14);
  display: flex;
  flex-direction: column;
}

.dh {
  padding: 20px 24px;
  border-bottom: 1px solid var(--cd-line);
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.dh h3 {
  font-size: 16px;
  font-weight: 700;
  color: var(--cd-text-1);
}

.x {
  cursor: pointer;
  color: var(--cd-text-3);
  display: flex;
}

.x:hover {
  color: var(--cd-text-1);
}

.db {
  flex: 1;
  overflow: auto;
  padding: 20px 24px;
  font-size: 13.5px;
  line-height: 1.75;
  color: var(--cd-text-2);
}

.field {
  margin-bottom: 16px;
}

.field label {
  display: block;
  font-size: 10.5px;
  color: var(--cd-text-3);
  font-family: 'SF Mono', 'Cascadia Code', Consolas, monospace;
  letter-spacing: 0.6px;
  margin-bottom: 5px;
}

.field .val {
  font-size: 13.5px;
  color: var(--cd-text-1);
  line-height: 1.75;
}

.field .val.box {
  background: var(--cd-panel);
  border: 1px solid var(--cd-line);
  border-radius: 12px;
  padding: 11px 14px;
}

.pack-tip {
  font-size: 12px;
  color: var(--cd-text-3);
  background: var(--cd-primary-soft);
  border-radius: 10px;
  padding: 10px 14px;
  margin-top: 4px;
}

.df {
  padding: 16px 24px;
  border-top: 1px solid var(--cd-line);
  display: flex;
  gap: 10px;
  justify-content: flex-end;
}
</style>
