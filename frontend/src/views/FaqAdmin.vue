<template>
  <div class="page">
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span class="card-title">FAQ 管理</span>
          <div class="filters">
            <el-input
              v-model="keyword"
              placeholder="按问题/关键词/答案筛选"
              clearable
              style="width: 240px"
            />
            <el-button type="primary" :icon="Plus" @click="openCreate">新增 FAQ</el-button>
            <el-button :icon="Refresh" @click="fetchList">刷新</el-button>
          </div>
        </div>
      </template>

      <el-table v-loading="loading" :data="filteredFaqs" row-key="id">
        <el-table-column label="ID" prop="id" width="70" />
        <el-table-column label="分类" width="100">
          <template #default="{ row }">
            <el-tag size="small" type="warning">{{ row.category }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="关键词" prop="keywords" min-width="140" show-overflow-tooltip />
        <el-table-column label="问题" prop="question" min-width="220" show-overflow-tooltip />
        <el-table-column label="答案" prop="answer" min-width="260" show-overflow-tooltip />
        <el-table-column label="操作" width="140" align="center">
          <template #default="{ row }">
            <el-button size="small" type="primary" plain @click="openEdit(row)">编辑</el-button>
            <el-button size="small" type="danger" plain @click="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-empty v-if="!loading && filteredFaqs.length === 0" description="暂无 FAQ" />
    </el-card>

    <!-- 新增/编辑弹窗 -->
    <el-dialog
      v-model="dialogVisible"
      :title="editingId ? '编辑 FAQ' : '新增 FAQ'"
      width="560px"
      @closed="resetForm"
    >
      <el-form label-position="top">
        <el-form-item label="分类" required>
          <el-input v-model="form.category" placeholder="如 网络/教务/密码/邮箱" />
        </el-form-item>
        <el-form-item label="关键词（逗号分隔）" required>
          <el-input v-model="form.keywords" placeholder="如 密码,重置,登录" />
        </el-form-item>
        <el-form-item label="问题" required>
          <el-input v-model="form.question" placeholder="学生常见问题" />
        </el-form-item>
        <el-form-item label="答案" required>
          <el-input v-model="form.answer" type="textarea" :rows="4" placeholder="标准解答" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="saving"
          :disabled="!formValid"
          @click="handleSave"
        >
          保存
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import { createFaq, deleteFaq, getFaqs, updateFaq } from '../api/faq'

const loading = ref(false)
const faqs = ref([])
const keyword = ref('')

const dialogVisible = ref(false)
const saving = ref(false)
const editingId = ref(null)
const form = ref({ category: '', keywords: '', question: '', answer: '' })

const filteredFaqs = computed(() => {
  const kw = keyword.value.trim()
  if (!kw) return faqs.value
  const lower = kw.toLowerCase()
  return faqs.value.filter(
    (f) =>
      f.question.toLowerCase().includes(lower) ||
      f.answer.toLowerCase().includes(lower) ||
      f.keywords.toLowerCase().includes(lower) ||
      f.category.toLowerCase().includes(lower)
  )
})

const formValid = computed(
  () =>
    form.value.category.trim() &&
    form.value.keywords.trim() &&
    form.value.question.trim() &&
    form.value.answer.trim()
)

async function fetchList() {
  loading.value = true
  try {
    const resp = await getFaqs()
    faqs.value = resp.data.items || []
  } catch {
    faqs.value = []
    ElMessage.error('加载 FAQ 列表失败')
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editingId.value = null
  dialogVisible.value = true
}

function openEdit(row) {
  editingId.value = row.id
  form.value = {
    category: row.category,
    keywords: row.keywords,
    question: row.question,
    answer: row.answer
  }
  dialogVisible.value = true
}

function resetForm() {
  editingId.value = null
  form.value = { category: '', keywords: '', question: '', answer: '' }
}

async function handleSave() {
  if (!formValid.value) return
  const payload = {
    category: form.value.category.trim(),
    keywords: form.value.keywords.trim(),
    question: form.value.question.trim(),
    answer: form.value.answer.trim()
  }
  saving.value = true
  try {
    if (editingId.value != null) {
      await updateFaq(editingId.value, payload)
      ElMessage.success('FAQ 已更新')
    } else {
      await createFaq(payload)
      ElMessage.success('FAQ 已新增')
    }
    dialogVisible.value = false
    fetchList()
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || '保存失败，请重试')
  } finally {
    saving.value = false
  }
}

async function handleDelete(row) {
  try {
    await ElMessageBox.confirm(`确认删除 FAQ「${row.question}」？`, '删除确认', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消'
    })
  } catch {
    return // 用户取消
  }
  try {
    await deleteFaq(row.id)
    ElMessage.success('FAQ 已删除')
    fetchList()
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || '删除失败，请重试')
  }
}

onMounted(fetchList)
</script>

<style scoped>
.page {
  height: 100%;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.card-title {
  font-size: 16px;
  font-weight: 600;
}

.filters {
  display: flex;
  align-items: center;
  gap: 10px;
}
</style>
