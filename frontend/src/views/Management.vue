<template>
  <div class="page">
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span class="card-title">工单管理</span>
          <div class="filters">
            <el-select
              v-model="statusFilter"
              placeholder="按状态筛选"
              clearable
              style="width: 160px"
              @change="handleFilterChange"
            >
              <el-option
                v-for="(meta, code) in STATUS_META"
                :key="code"
                :label="meta.label"
                :value="code"
              />
            </el-select>
            <el-button :icon="Refresh" @click="fetchList">刷新</el-button>
          </div>
        </div>
      </template>

      <el-table
        v-loading="loading"
        :data="tickets"
        row-key="id"
        @row-click="openDetail"
      >
        <el-table-column label="单号" width="110">
          <template #default="{ row }">
            <span
              class="link-like"
              :class="{ 'upgrade-id': row.escalation_count > 0 }"
              :title="row.escalation_count > 0 ? '该工单已超时升级' : ''"
            >
              #{{ row.id }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="类别" width="110">
          <template #default="{ row }">
            <span>{{ row.category || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="类型" width="100">
          <template #default="{ row }">
            <el-tag :type="typeMeta(row.ticket_type).type" size="small">
              {{ typeMeta(row.ticket_type).label }}
            </el-tag>
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
        <el-table-column label="楼栋" prop="building" min-width="110" />
        <el-table-column label="部门" prop="dept" min-width="110" />
        <el-table-column label="创建时间" prop="created_at" width="160" />
        <el-table-column label="操作" width="120" align="center">
          <template #default="{ row }">
            <el-button
              v-if="['SUBMITTED', 'ASSIGNED'].includes(row.status)"
              size="small"
              type="primary"
              plain
              @click.stop="openAssign(row)"
            >
              派单
            </el-button>
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

    <!-- 派单弹窗 -->
    <el-dialog
      v-model="assignVisible"
      :title="assignTarget ? `派单 · 工单 #${assignTarget.id}` : '派单'"
      width="480px"
      @closed="resetAssign"
    >
      <el-form label-position="top">
        <el-form-item label="维修人员">
          <el-select
            v-model="assignRepairmanId"
            placeholder="请选择维修人员"
            filterable
            style="width: 100%"
          >
            <el-option
              v-for="staff in staffList"
              :key="staff.id"
              :label="`${staff.name} · ${staff.dept} · ${staff.trade || '维修工'}`"
              :value="staff.id"
            />
          </el-select>
        </el-form-item>
      </el-form>
      <div class="assign-hint">派单后工单将进入「已派单」状态，维修人会收到通知。</div>
      <template #footer>
        <el-button @click="assignVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="assigning"
          :disabled="!assignRepairmanId"
          @click="handleAssign"
        >
          确认派单
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import {
  getTickets,
  getTicket,
  assignTicket,
  getStaff
} from '../api/tickets'
import {
  STATUS_META,
  statusMeta,
  priorityMeta,
  typeMeta
} from '../constants/status'

const loading = ref(false)
const tickets = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = 20
const statusFilter = ref('')

const drawerVisible = ref(false)
const detailLoading = ref(false)
const detail = ref(null)
const detailId = ref(null)

const assignVisible = ref(false)
const assigning = ref(false)
const assignTarget = ref(null)
const assignRepairmanId = ref(null)
const staffList = ref([])

const detailTitle = computed(() =>
  detailId.value != null ? `工单详情 #${detailId.value}` : '工单详情'
)

async function fetchList() {
  loading.value = true
  try {
    const resp = await getTickets({
      status: statusFilter.value || undefined,
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

function handleFilterChange() {
  page.value = 1
  fetchList()
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
    ElMessage.error('加载详情失败')
    drawerVisible.value = false
  } finally {
    detailLoading.value = false
  }
}

async function openAssign(row) {
  assignTarget.value = row
  assignVisible.value = true
  if (staffList.value.length === 0) {
    try {
      const resp = await getStaff()
      staffList.value = resp.data || []
    } catch {
      ElMessage.error('加载维修人员列表失败')
    }
  }
}

function resetAssign() {
  assignTarget.value = null
  assignRepairmanId.value = null
}

async function handleAssign() {
  if (!assignTarget.value || !assignRepairmanId.value) return
  const staff = staffList.value.find((s) => s.id === assignRepairmanId.value)
  assigning.value = true
  try {
    await assignTicket(assignTarget.value.id, {
      repairman_id: assignRepairmanId.value,
      dept: staff ? staff.dept : ''
    })
    ElMessage.success(`已派单给 ${staff ? staff.name : ''}`)
    assignVisible.value = false
    fetchList()
  } catch {
    ElMessage.error('派单失败，请重试')
  } finally {
    assigning.value = false
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

.link-like {
  color: #409eff;
  cursor: pointer;
}

.upgrade-id {
  color: #f56c6c;
  font-weight: 600;
}

.no-op {
  color: #c0c4cc;
}

.pager {
  margin-top: 16px;
  display: flex;
  justify-content: flex-end;
}

.assign-hint {
  font-size: 12px;
  color: #909399;
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

.timeline-item {
  font-size: 13px;
  color: #303133;
}

.timeline-sub {
  font-size: 12px;
  color: #909399;
  margin-top: 2px;
}

.logs-hint {
  font-size: 12px;
  color: #909399;
  margin-top: 4px;
}
</style>
