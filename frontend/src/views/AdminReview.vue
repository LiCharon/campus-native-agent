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
        <select v-model="kfDomain" @change="loadKnowledge">
          <option value="">全部领域</option>
          <option v-for="d in DOMAINS" :key="d" :value="d">{{ d }}</option>
        </select>
        <select v-model="kfType" @change="loadKnowledge">
          <option value="">全部类型</option>
          <option value="info">info 知识型</option>
          <option value="process">process 流程型</option>
          <option value="index">index 索引型</option>
        </select>
        <input v-model="kfQ" placeholder="搜索问题 / 关键词…" @keydown.enter="loadKnowledge" />
        <button class="cd-btn sm" @click="loadKnowledge">搜索</button>
        <button class="cd-btn sm" @click="openCreate">新建</button>
      </div>
      <p v-if="kbTruncated" class="cd-tip">
        当前仅显示前 {{ knowledge.length }} 条（共 {{ kbTotal }} 条匹配）。请用上方「领域 / 类型 / 关键词」筛选缩小范围。
      </p>
      <div class="cd-table-card">
        <table>
          <thead>
            <tr><th style="width:28%">问题</th><th>领域</th><th>类型</th><th>答案摘要</th><th style="width:140px">操作</th></tr>
          </thead>
          <tbody>
            <tr v-for="k in knowledge" :key="k.id">
              <td>{{ k.question }}</td>
              <td>{{ k.domain }}</td>
              <td><span class="cd-tag" :class="typeTag(k.type)">{{ k.type }}</span></td>
              <td class="answer">{{ k.answer }}</td>
              <td>
                <div class="kb-act">
                  <button class="cd-btn sm" @click="openEdit(k)">编辑</button>
                  <button class="cd-btn ghost sm" @click="handleDelete(k)">删除</button>
                </div>
              </td>
            </tr>
            <tr v-if="!knowledge.length"><td colspan="5"><div class="cd-empty">暂无知识条目</div></td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- 补入弹窗 -->
    <div class="overlay" :class="{ open: dialogVisible }" @click.self="dialogVisible = false">
      <div class="modal-box">
        <div class="mh">{{ dialogMode === 'adopt' ? '补入知识库' : dialogMode === 'edit' ? '编辑知识条目' : '新建知识条目' }}</div>
        <div class="mb">
          <div>
            <label>问题</label>
            <input v-if="dialogMode === 'adopt'" :value="form.question" disabled />
            <input v-else v-model="form.question" placeholder="填写问题" />
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
          <button class="cd-btn" :disabled="submitting" @click="handleSubmit">{{ dialogMode === 'adopt' ? '确认补入' : dialogMode === 'edit' ? '保存修改' : '创建条目' }}</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { createKnowledge, deleteKnowledge, fetchKnowledge, updateKnowledge } from '../api/admin'
import { adoptReview, dismissReview, fetchReviews } from '../api/reviews'

const DOMAINS = ['教务', '图书馆', '网络与IT', '校园卡与证件', '住宿后勤', '奖助', '医疗健康', '社团与活动', '就业与职业发展', '安全与保卫', '生活服务']

const activeTab = ref('bad_cases')
const items = ref([])
const knowledge = ref([])
const kfDomain = ref('')
const kfType = ref('')
const kfQ = ref('')
// BUG-003 截断告知：后端默认 limit=200，超限时提示用筛选缩小范围
const kbTotal = ref(0)
const kbTruncated = ref(false)
const dialogVisible = ref(false)
const dialogMode = ref('adopt') // adopt 补入 / create 新建 / edit 编辑
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
    kbTotal.value = resp.data.total ?? knowledge.value.length
    kbTruncated.value = !!resp.data.truncated
  } catch {
    ElMessage.error('加载失败，请检查后端是否启动')
    knowledge.value = []
    kbTotal.value = 0
    kbTruncated.value = false
  }
}

function openAdopt(row) {
  dialogMode.value = 'adopt'
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

function openCreate() {
  dialogMode.value = 'create'
  Object.assign(form, { question: '', domain: '', type: 'info', keywords: '', answer: '' })
  dialogVisible.value = true
}

function openEdit(row) {
  dialogMode.value = 'edit'
  currentRow.value = row
  Object.assign(form, {
    question: row.question,
    domain: row.domain,
    type: row.type,
    keywords: row.keywords,
    answer: row.answer
  })
  dialogVisible.value = true
}

async function handleSubmit() {
  if (!form.question.trim() || !form.domain || !form.keywords.trim() || !form.answer.trim()) {
    ElMessage.warning('请完整填写问题、领域、关键词与答案')
    return
  }
  submitting.value = true
  try {
    if (dialogMode.value === 'adopt') {
      await adoptReview(activeTab.value, currentRow.value.id, {
        domain: form.domain,
        type: form.type,
        keywords: form.keywords.trim(),
        answer: form.answer.trim()
      })
      ElMessage.success('已补入知识库')
    } else {
      const payload = {
        domain: form.domain,
        type: form.type,
        question: form.question.trim(),
        keywords: form.keywords.trim(),
        answer: form.answer.trim()
      }
      if (dialogMode.value === 'edit') {
        await updateKnowledge(currentRow.value.id, payload)
        ElMessage.success('已保存修改')
      } else {
        await createKnowledge(payload)
        ElMessage.success('已创建条目')
      }
    }
    dialogVisible.value = false
    await loadTab()
  } catch (e) {
    ElMessage.error((e.response && e.response.data && e.response.data.detail) || '操作失败')
  } finally {
    submitting.value = false
  }
}

async function handleDelete(row) {
  try {
    await ElMessageBox.confirm(`确定删除「${row.question}」？删除后不可恢复。`, '删除确认', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消'
    })
  } catch {
    return
  }
  try {
    await deleteKnowledge(row.id)
    ElMessage.success('已删除')
    await loadKnowledge()
  } catch (e) {
    ElMessage.error((e.response && e.response.data && e.response.data.detail) || '删除失败')
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

.cd-tip {
  margin: 0 0 12px;
  padding: 9px 13px;
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--cd-text-2);
  background: var(--cd-bg-soft, #f3f6fb);
  border-left: 3px solid var(--cd-primary, #14549c);
  border-radius: 4px;
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
