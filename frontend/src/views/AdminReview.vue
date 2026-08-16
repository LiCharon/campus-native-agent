<template>
  <div class="review-page">
    <div class="review-head">
      <h3 class="review-title">知识库管理 · 待审内容</h3>
      <el-button :icon="Refresh" circle @click="load" :loading="loading" title="刷新" />
    </div>

    <el-tabs v-model="activeTab" @tab-change="load">
      <el-tab-pane label="未解决反馈" name="bad_cases" />
      <el-tab-pane label="用户提议" name="suggestions" />
    </el-tabs>

    <el-table v-loading="loading" :data="items" border stripe empty-text="暂无待审内容">
      <el-table-column prop="question" label="问题" min-width="240" show-overflow-tooltip />
      <el-table-column v-if="activeTab === 'bad_cases'" prop="reply" label="Agent 回复" min-width="200" show-overflow-tooltip />
      <el-table-column prop="note" label="补充说明" min-width="140" show-overflow-tooltip>
        <template #default="{ row }">{{ row.note || '—' }}</template>
      </el-table-column>
      <el-table-column prop="user_id" label="提交人" width="120" />
      <el-table-column prop="created_at" label="时间" width="170">
        <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="170" fixed="right">
        <template #default="{ row }">
          <el-button type="primary" link size="small" @click="openAdopt(row)">补入知识库</el-button>
          <el-button type="danger" link size="small" @click="handleDismiss(row)">驳回</el-button>
        </template>
      </el-table-column>
      <template #empty>
        <el-empty :image-size="60" description="暂无待审内容" />
      </template>
    </el-table>

    <!-- 补入知识库弹窗 -->
    <el-dialog v-model="dialogVisible" title="补入知识库" width="560px" :close-on-click-modal="false">
      <el-form label-width="90px">
        <el-form-item label="问题">
          <el-input :model-value="form.question" disabled />
        </el-form-item>
        <el-form-item label="领域" required>
          <el-select v-model="form.domain" placeholder="选择领域" style="width: 100%">
            <el-option v-for="d in DOMAINS" :key="d" :label="d" :value="d" />
          </el-select>
        </el-form-item>
        <el-form-item label="类型" required>
          <el-select v-model="form.type" style="width: 100%">
            <el-option label="直接回答" value="info" />
            <el-option label="流程清单" value="process" />
            <el-option label="索引引导" value="index" />
          </el-select>
        </el-form-item>
        <el-form-item label="关键词" required>
          <el-input v-model="form.keywords" placeholder="逗号分隔，如：导师,研究生" />
          <div class="form-tip">已按问题自动预填，可修改（决定检索命中）</div>
        </el-form-item>
        <el-form-item label="答案" required>
          <el-input v-model="form.answer" type="textarea" :rows="4" placeholder="填写知识答案" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleAdopt">确认补入</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { adoptReview, dismissReview, fetchReviews } from '../api/reviews'

const DOMAINS = ['教务', '后勤', '图书馆', 'IT', '证件', '生活']

const activeTab = ref('bad_cases')
const items = ref([])
const loading = ref(false)
const dialogVisible = ref(false)
const submitting = ref(false)
const form = ref({ question: '', domain: '', type: 'info', keywords: '', answer: '' })
let currentRow = null

async function load() {
  loading.value = true
  try {
    const resp = await fetchReviews(activeTab.value)
    items.value = resp.data.items || []
  } catch {
    ElMessage.error('加载失败，请检查后端是否启动')
    items.value = []
  } finally {
    loading.value = false
  }
}

function openAdopt(row) {
  currentRow = row
  form.value = {
    question: row.question,
    domain: '',
    type: 'info',
    keywords: row.suggested_keywords || '',
    answer: ''
  }
  dialogVisible.value = true
}

async function handleAdopt() {
  if (!form.value.domain || !form.value.keywords.trim() || !form.value.answer.trim()) {
    ElMessage.warning('请完整填写领域、关键词与答案')
    return
  }
  submitting.value = true
  try {
    await adoptReview(activeTab.value, currentRow.id, {
      domain: form.value.domain,
      type: form.value.type,
      keywords: form.value.keywords.trim(),
      answer: form.value.answer.trim()
    })
    ElMessage.success('已补入知识库')
    dialogVisible.value = false
    await load()
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
    await load()
  } catch {
    ElMessage.error('操作失败，请稍后重试')
  }
}

function formatTime(ts) {
  if (!ts) return ''
  return new Date(ts).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  })
}

onMounted(load)
</script>

<style scoped>
.review-page {
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  padding: 16px;
}

.review-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.review-title {
  margin: 0;
  font-size: 16px;
  color: #303133;
}

.form-tip {
  font-size: 12px;
  color: #909399;
  margin-top: 4px;
}
</style>
