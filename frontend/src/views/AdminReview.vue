<template>
  <div class="page">
    <div class="cd-ph">
      <div>
        <h2>知识库管理</h2>
        <p>审查补入（采纳 / 驳回）＋ 知识库浏览</p>
      </div>
      <button class="cd-btn ghost" @click="loadTab">
        <svg viewBox="0 0 24 24" class="ic"><path d="M4 4v6h6M20 20v-6h-6"/><path d="M4 10a8 8 0 0 1 14-3M20 14a8 8 0 0 1-14 3"/></svg>
        刷新
      </button>
    </div>

    <div class="tabs">
      <div class="tab" :class="{ active: activeTab === 'bad_cases' }" @click="switchTab('bad_cases')">未解决反馈</div>
      <div class="tab" :class="{ active: activeTab === 'suggestions' }" @click="switchTab('suggestions')">用户提议</div>
      <div class="tab" :class="{ active: activeTab === 'browse' }" @click="switchTab('browse')">知识库浏览</div>
    </div>

    <!-- 审查 tab -->
    <div v-if="activeTab !== 'browse'" class="content">
      <div class="cd-table-card">
        <table>
          <thead>
            <tr><th style="width:34%">问题 / 建议</th><th style="width:20%">Agent 回复</th><th>补充说明</th><th>提交人</th><th>时间</th><th style="width:170px">操作</th></tr>
          </thead>
          <tbody>
            <tr v-for="row in items" :key="row.id">
              <td>{{ row.question }}</td>
              <td class="mono">{{ row.reply || '—（转人工）' }}</td>
              <td class="mono">{{ row.note || '—' }}</td>
              <td class="mono">{{ row.user_id }}</td>
              <td class="mono">{{ formatTime(row.created_at) }}</td>
              <td>
                <div class="kb-act">
                  <button class="cd-btn sm" @click="openAdopt(row)">采纳补入</button>
                  <button class="cd-btn danger sm" @click="handleDismiss(row)">驳回</button>
                </div>
              </td>
            </tr>
            <tr v-if="!items.length">
              <td colspan="6"><div class="cd-empty">暂无待审内容</div></td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- 浏览 tab -->
    <div v-else class="content">
      <div class="cd-filters">
        <select v-model="kfDomain">
          <option value="">全部领域</option>
          <option v-for="d in DOMAINS" :key="d" :value="d">{{ d }}</option>
        </select>
        <select v-model="kfType">
          <option value="">全部类型</option>
          <option value="info">info 知识型</option>
          <option value="process">process 流程型</option>
          <option value="index">index 索引型</option>
        </select>
        <input v-model="kfQ" placeholder="搜索问题 / 关键词…" @keydown.enter="loadKnowledge" />
        <button class="cd-btn sm" @click="loadKnowledge">搜索</button>
      </div>
      <div class="cd-table-card">
        <table>
          <thead>
            <tr><th style="width:30%">问题</th><th>领域</th><th>类型</th><th>答案摘要</th></tr>
          </thead>
          <tbody>
            <tr v-for="k in knowledge" :key="k.id">
              <td>{{ k.question }}</td>
              <td>{{ k.domain }}</td>
              <td><span class="cd-tag" :class="typeTag(k.type)">{{ k.type }}</span></td>
              <td class="answer">{{ k.answer }}</td>
            </tr>
            <tr v-if="!knowledge.length"><td colspan="4"><div class="cd-empty">暂无知识条目</div></td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- 补入弹窗 -->
    <div class="overlay" :class="{ open: dialogVisible }" @click.self="dialogVisible = false">
      <div class="modal-box">
        <div class="mh">补入知识库</div>
        <div class="mb">
          <div>
            <label>问题</label>
            <input :value="form.question" disabled />
          </div>
          <div>
            <label>领域</label>
            <select v-model="form.domain">
              <option value="" disabled>选择领域</option>
              <option v-for="d in DOMAINS" :key="d" :value="d">{{ d }}</option>
            </select>
          </div>
          <div>
            <label>类型</label>
            <select v-model="form.type">
              <option value="info">info 直接答</option>
              <option value="process">process 流程清单</option>
              <option value="index">index 索引引导</option>
            </select>
          </div>
          <div>
            <label>关键词（已预填，可修改）</label>
            <input v-model="form.keywords" placeholder="逗号分隔，如：导师,研究生" />
          </div>
          <div>
            <label>答案</label>
            <textarea v-model="form.answer" rows="4" placeholder="填写知识答案"></textarea>
          </div>
        </div>
        <div class="mf">
          <button class="cd-btn ghost" @click="dialogVisible = false">取消</button>
          <button class="cd-btn" :disabled="submitting" @click="handleAdopt">确认补入</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { fetchKnowledge } from '../api/admin'
import { adoptReview, dismissReview, fetchReviews } from '../api/reviews'

const DOMAINS = ['教务', '后勤', '图书馆', 'IT', '证件', '生活']

const activeTab = ref('bad_cases')
const items = ref([])
const knowledge = ref([])
const kfDomain = ref('')
const kfType = ref('')
const kfQ = ref('')
const dialogVisible = ref(false)
const submitting = ref(false)
const currentRow = ref(null)
const form = reactive({ question: '', domain: '', type: 'info', keywords: '', answer: '' })

async function loadTab() {
  if (activeTab.value === 'browse') {
    await loadKnowledge()
  } else {
    try {
      const resp = await fetchReviews(activeTab.value)
      items.value = resp.data.items || []
    } catch {
      ElMessage.error('加载失败，请检查后端是否启动')
      items.value = []
    }
  }
}

function switchTab(tab) {
  activeTab.value = tab
  loadTab()
}

async function loadKnowledge() {
  try {
    const params = {}
    if (kfDomain.value) params.domain = kfDomain.value
    if (kfType.value) params.type = kfType.value
    if (kfQ.value.trim()) params.q = kfQ.value.trim()
    const resp = await fetchKnowledge(params)
    knowledge.value = resp.data.items || []
  } catch {
    ElMessage.error('加载失败，请检查后端是否启动')
    knowledge.value = []
  }
}

function openAdopt(row) {
  currentRow.value = row
  Object.assign(form, {
    question: row.question,
    domain: '',
    type: 'info',
    keywords: row.suggested_keywords || '',
    answer: ''
  })
  dialogVisible.value = true
}

async function handleAdopt() {
  if (!form.domain || !form.keywords.trim() || !form.answer.trim()) {
    ElMessage.warning('请完整填写领域、关键词与答案')
    return
  }
  submitting.value = true
  try {
    await adoptReview(activeTab.value, currentRow.value.id, {
      domain: form.domain,
      type: form.type,
      keywords: form.keywords.trim(),
      answer: form.answer.trim()
    })
    ElMessage.success('已补入知识库')
    dialogVisible.value = false
    await loadTab()
  } catch (e) {
    ElMessage.error((e.response && e.response.data && e.response.data.detail) || '补入失败')
  } finally {
    submitting.value = false
  }
}

async function handleDismiss(row) {
  try {
    await ElMessageBox.confirm('确定驳回？该条不会进入知识库。', '驳回确认', {
      type: 'warning',
      confirmButtonText: '驳回',
      cancelButtonText: '取消'
    })
  } catch {
    return
  }
  try {
    await dismissReview(activeTab.value, row.id)
    ElMessage.success('已驳回')
    await loadTab()
  } catch {
    ElMessage.error('操作失败，请稍后重试')
  }
}

function typeTag(t) {
  return t === 'process' ? 'amber' : t === 'index' ? 'gray' : ''
}

function formatTime(ts) {
  if (!ts) return ''
  return new Date(ts).toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'
  })
}

onMounted(loadTab)
</script>

<style scoped>
.page {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
  background: var(--cd-bg);
}

.tabs {
  display: flex;
  gap: 2px;
  padding: 0 28px;
  border-bottom: 1px solid var(--cd-line);
  background: var(--cd-card);
}

.tab {
  padding: 13px 15px;
  font-size: 13.5px;
  color: var(--cd-text-2);
  cursor: pointer;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
}

.tab:hover {
  color: var(--cd-primary);
}

.tab.active {
  color: var(--cd-primary);
  font-weight: 600;
  border-bottom-color: var(--cd-primary);
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

.answer {
  color: var(--cd-text-2);
  font-size: 13px;
  max-width: 340px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.kb-act {
  display: flex;
  gap: 9px;
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

.cd-filters select,
.cd-filters input {
  font-family: inherit;
  font-size: 13px;
  padding: 9px 12px;
  border: 1px solid var(--cd-line);
  border-radius: var(--cd-radius-btn);
  background: #fff;
  color: var(--cd-text-1);
}

.cd-filters input {
  min-width: 220px;
}

.overlay {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.45);
  display: none;
  z-index: 50;
  align-items: center;
  justify-content: center;
}

.overlay.open {
  display: flex;
}

.modal-box {
  width: 520px;
  max-width: 94vw;
  background: #fff;
  border-radius: 16px;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.25);
  overflow: hidden;
}

.mh {
  padding: 20px 24px;
  border-bottom: 1px solid var(--cd-line);
  font-size: 16px;
  font-weight: 700;
  color: var(--cd-text-1);
}

.mb {
  padding: 22px 24px;
  display: grid;
  gap: 15px;
}

.mb label {
  font-size: 12.5px;
  color: var(--cd-text-2);
  display: block;
  margin-bottom: 7px;
  font-weight: 600;
}

.mb input,
.mb select,
.mb textarea {
  width: 100%;
  font-family: inherit;
  font-size: 13.5px;
  padding: 10px 12px;
  border: 1px solid var(--cd-line);
  border-radius: var(--cd-radius-btn);
  background: #fff;
  color: var(--cd-text-1);
  resize: vertical;
}

.mb input:focus,
.mb select:focus,
.mb textarea:focus {
  outline: none;
  border-color: var(--cd-primary);
  box-shadow: 0 0 0 3px rgba(20, 84, 156, 0.1);
}

.mf {
  padding: 16px 24px;
  border-top: 1px solid var(--cd-line);
  display: flex;
  gap: 10px;
  justify-content: flex-end;
}
</style>
