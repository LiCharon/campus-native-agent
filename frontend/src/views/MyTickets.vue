<template>
  <div class="page">
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span class="card-title">我的工单</span>
          <el-button :icon="Refresh" @click="fetchList">刷新</el-button>
        </div>
      </template>

      <el-table
        v-loading="loading"
        :data="tickets"
        row-key="id"
        @row-click="openDetail"
      >
        <el-table-column label="单号" prop="id" width="110">
          <template #default="{ row }">
            <span class="link-like">#{{ row.id }}</span>
          </template>
        </el-table-column>
        <el-table-column label="类别" width="110">
          <template #default="{ row }">
            <span>{{ row.category || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="优先级" width="100">
          <template #default="{ row }">
            <el-tag :type="priorityMeta(row.priority).type" size="small">
              {{ priorityMeta(row.priority).label }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag :type="statusMeta(row.status).type" size="small">
              {{ statusMeta(row.status).label }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="楼栋" prop="building" min-width="120" />
        <el-table-column label="创建时间" prop="created_at" width="160" />
        <el-table-column label="操作" width="170" align="center">
          <template #default="{ row }">
            <template v-if="canOperate(row)">
              <el-button
                size="small"
                type="success"
                plain
                @click.stop="handleVerify(row)"
              >
                验收
              </el-button>
              <el-button
                size="small"
                type="danger"
                plain
                @click.stop="handleCancel(row)"
              >
                撤回
              </el-button>
            </template>
            <span v-else class="no-op">—</span>
          </template>
        </el-table-column>
      </el-table>

      <el-empty v-if="!loading && tickets.length === 0" description="暂无工单" />

      <div class="pager">
        <el-pagination
          v-model:current-page="page"
          :page-size="pageSize"
          :total="total"
          layout="total, prev, pager, next"
          @current-change="fetchList"
        />
      </div>
    </el-card>

    <!-- 详情抽屉 -->
    <el-drawer v-model="drawerVisible" size="520px" :title="detailTitle">
      <div v-loading="detailLoading">
        <template v-if="detail">
          <el-descriptions :column="2" border>
            <el-descriptions-item label="单号">#{{ detail.id }}</el-descriptions-item>
            <el-descriptions-item label="类别">{{ detail.category || '—' }}</el-descriptions-item>
            <el-descriptions-item label="优先级">
              <el-tag :type="priorityMeta(detail.priority).type" size="small">
                {{ priorityMeta(detail.priority).label }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="状态">
              <el-tag :type="statusMeta(detail.status).type" size="small">
                {{ statusMeta(detail.status).label }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="楼栋">{{ detail.building }}</el-descriptions-item>
            <el-descriptions-item label="部门">{{ detail.dept || '-' }}</el-descriptions-item>
            <el-descriptions-item label="联系方式">{{ detail.contact || '-' }}</el-descriptions-item>
            <el-descriptions-item label="维修人">
              {{ detail.repairman_name || '-' }}
            </el-descriptions-item>
            <el-descriptions-item label="升级次数">
              <span :class="{ 'upgrade-mark': detail.escalation_count > 0 }">
                {{ detail.escalation_count || 0 }}
              </span>
            </el-descriptions-item>
            <el-descriptions-item label="回访评分">
              {{ detail.rating != null ? `${detail.rating} 分` : '未回访' }}
            </el-descriptions-item>
          </el-descriptions>

          <div class="detail-section">
            <div class="section-title">问题描述</div>
            <div class="desc-text">{{ detail.description || '-' }}</div>
          </div>

          <div v-if="detail.review_comment" class="detail-section">
            <div class="section-title">回访意见</div>
            <div class="desc-text">{{ detail.review_comment }}</div>
          </div>

          <div class="detail-section">
            <div class="section-title">时间线</div>
            <el-timeline class="timeline">
              <el-timeline-item :timestamp="detail.created_at" placement="top">
                <div class="timeline-item">创建工单</div>
                <div v-if="detail.repairman_name" class="timeline-sub">已记录，等待处理</div>
                <div v-else class="timeline-sub">已记录，等待派单</div>
              </el-timeline-item>
              <el-timeline-item
                v-if="detail.escalated_at"
                :timestamp="detail.escalated_at"
                placement="top"
                type="danger"
              >
                <div class="timeline-item">超时升级</div>
              </el-timeline-item>
              <el-timeline-item
                v-if="detail.closed_at"
                :timestamp="detail.closed_at"
                placement="top"
                type="success"
              >
                <div class="timeline-item">工单关闭</div>
              </el-timeline-item>
            </el-timeline>
            <div class="logs-hint">共 {{ detail.logs_count || 0 }} 条处理日志</div>
          </div>
        </template>
        <el-empty v-else description="无详情数据" />
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { getTickets, getTicket, verifyTicket, cancelTicket } from '../api/tickets'
import { statusMeta, priorityMeta, typeMeta, TERMINAL_STATUS } from '../constants/status'

const loading = ref(false)
const tickets = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = 20

const drawerVisible = ref(false)
const detailLoading = ref(false)
const detail = ref(null)
const detailId = ref(null)

const detailTitle = computed(() =>
  detailId.value != null ? `工单详情 #${detailId.value}` : '工单详情'
)

// 终态（CLOSED/CANCELLED）不可操作
function canOperate(row) {
  return !TERMINAL_STATUS.includes(row.status)
}

async function fetchList() {
  loading.value = true
  try {
    const resp = await getTickets({
      limit: pageSize,
      offset: (page.value - 1) * pageSize
    })
    tickets.value = resp.data.items || []
    total.value = resp.data.total || 0
  } catch {
    tickets.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

async function openDetail(row) {
  detailId.value = row.id
  drawerVisible.value = true
  detailLoading.value = true
  detail.value = null
  try {
    const resp = await getTicket(row.id)
    detail.value = resp.data
  } catch {
    ElMessage.error('加载详情失败（可能已无权限或工单不存在）')
    drawerVisible.value = false
  } finally {
    detailLoading.value = false
  }
}

async function handleVerify(row) {
  try {
    await ElMessageBox.confirm(
      `确认验收工单 #${row.id}？验收后工单将关闭。`,
      '验收确认',
      { type: 'success', confirmButtonText: '确认验收', cancelButtonText: '取消' }
    )
  } catch {
    return
  }
  await verifyTicket(row.id)
  ElMessage.success('验收成功，工单已关闭')
  fetchList()
}

async function handleCancel(row) {
  try {
    await ElMessageBox.confirm(
      `确认撤回工单 #${row.id}？撤回后工单将取消。`,
      '撤回确认',
      { type: 'warning', confirmButtonText: '确认撤回', cancelButtonText: '取消' }
    )
  } catch {
    return
  }
  await cancelTicket(row.id)
  ElMessage.success('工单已撤回')
  fetchList()
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

.link-like {
  color: #409eff;
  cursor: pointer;
}

.no-op {
  color: #c0c4cc;
}

.pager {
  margin-top: 16px;
  display: flex;
  justify-content: flex-end;
}

.upgrade-mark {
  color: #f56c6c;
  font-weight: 600;
}

.detail-section {
  margin-top: 20px;
}

.section-title {
  font-size: 14px;
  font-weight: 600;
  color: #303133;
  margin-bottom: 8px;
  padding-left: 8px;
  border-left: 3px solid #409eff;
}

.desc-text {
  background: #f5f7fa;
  border-radius: 6px;
  padding: 10px 12px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
}

.timeline {
  padding-left: 4px;
}

.logs-hint {
  font-size: 12px;
  color: #909399;
  margin-top: 4px;
}

.timeline-item {
  font-size: 13px;
  color: #303133;
}

.timeline-sub {
  font-size: 12px;
  color: #909399;
  margin-top: 2px;
}
</style>
