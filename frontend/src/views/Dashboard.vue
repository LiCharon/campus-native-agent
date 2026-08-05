<template>
  <div class="page" v-loading="loading">
    <!-- 统计卡片 -->
    <el-row :gutter="16">
      <el-col v-for="card in statCards" :key="card.label" :span="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-value" :style="{ color: card.color }">{{ card.value }}</div>
          <div class="stat-label">{{ card.label }}</div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 图表区 -->
    <el-row :gutter="16" class="chart-row">
      <el-col :span="10">
        <el-card shadow="never" class="chart-card">
          <template #header>工单状态分布</template>
          <div ref="statusChartRef" class="chart-box" />
        </el-card>
      </el-col>
      <el-col :span="7">
        <el-card shadow="never" class="chart-card">
          <template #header>优先级分布</template>
          <div ref="priorityChartRef" class="chart-box" />
        </el-card>
      </el-col>
      <el-col :span="7">
        <el-card shadow="never" class="chart-card">
          <template #header>类别分布（前 6）</template>
          <div ref="categoryChartRef" class="chart-box" />
        </el-card>
      </el-col>
    </el-row>

    <el-empty v-if="!loading && !dataLoaded" description="暂无统计数据" />
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import * as echarts from 'echarts'
import { getDashboard } from '../api/tickets'
import {
  STATUS_META,
  PRIORITY_META,
  typeMeta
} from '../constants/status'

const loading = ref(false)
const dataLoaded = ref(false)
const dashboard = ref({ total: 0, by_status: {}, by_priority: {}, by_category: {} })

const statusChartRef = ref(null)
const priorityChartRef = ref(null)
const categoryChartRef = ref(null)

let statusChart = null
let priorityChart = null
let categoryChart = null

// 顶部 4 卡片：总数/待派单/维修中/已关闭（由 total + by_status 计算）
const statCards = computed(() => {
  const byStatus = dashboard.value.by_status || {}
  return [
    { label: '总工单', value: dashboard.value.total || 0, color: '#409eff' },
    { label: '待派单', value: byStatus.SUBMITTED || 0, color: '#909399' },
    { label: '维修中', value: byStatus.IN_PROGRESS || 0, color: '#409eff' },
    { label: '已关闭', value: byStatus.CLOSED || 0, color: '#67c23a' }
  ]
})

function renderCharts() {
  if (!statusChartRef.value || !priorityChartRef.value || !categoryChartRef.value) return

  const byStatus = dashboard.value.by_status || {}
  const byPriority = dashboard.value.by_priority || {}
  const byCategory = dashboard.value.by_category || {}

  // 状态分布饼图（非空状态才显示）
  const statusData = Object.entries(byStatus)
    .filter(([, v]) => v > 0)
    .map(([code, value]) => ({
      name: STATUS_META[code] ? STATUS_META[code].label : code,
      value
    }))

  statusChart = statusChart || echarts.init(statusChartRef.value)
  statusChart.setOption({
    tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
    legend: { bottom: 0, type: 'scroll' },
    series: [
      {
        type: 'pie',
        radius: ['38%', '62%'],
        center: ['50%', '45%'],
        avoidLabelOverlap: true,
        itemStyle: { borderRadius: 6, borderColor: '#fff', borderWidth: 2 },
        label: { show: true, formatter: '{b}\n{c}' },
        data: statusData.length ? statusData : [{ name: '暂无数据', value: 1, itemStyle: { color: '#e4e7ed' } }]
      }
    ]
  })

  // 优先级柱状图（固定 P1/P2/P3，缺的补 0）
  const priorityData = ['P1', 'P2', 'P3'].map((code) => ({
    name: PRIORITY_META[code].label,
    value: byPriority[code] || 0
  }))

  priorityChart = priorityChart || echarts.init(priorityChartRef.value)
  priorityChart.setOption({
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 40, right: 16, top: 20, bottom: 30 },
    xAxis: {
      type: 'category',
      data: priorityData.map((d) => d.name),
      axisLabel: { color: '#606266' }
    },
    yAxis: { type: 'value', minInterval: 1, axisLabel: { color: '#909399' } },
    series: [
      {
        type: 'bar',
        barWidth: 44,
        data: priorityData.map((d) => ({
          value: d.value,
          itemStyle: {
            color: d.name === '紧急' ? '#f56c6c' : d.name === '普通' ? '#409eff' : '#c0c4cc',
            borderRadius: [4, 4, 0, 0]
          }
        })),
        label: { show: true, position: 'top' }
      }
    ]
  })

  // 类别分布柱状图（前 6 类）
  const categoryEntries = Object.entries(byCategory)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 6)

  categoryChart = categoryChart || echarts.init(categoryChartRef.value)
  categoryChart.setOption({
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 40, right: 16, top: 20, bottom: 44 },
    xAxis: {
      type: 'category',
      data: categoryEntries.map(([code]) => {
        const meta = typeMeta(code)
        return meta.label === code ? (code.length > 4 ? `${code.slice(0, 4)}…` : code) : meta.label
      }),
      axisLabel: { color: '#606266', interval: 0, rotate: 20 }
    },
    yAxis: { type: 'value', minInterval: 1, axisLabel: { color: '#909399' } },
    series: [
      {
        type: 'bar',
        barMaxWidth: 40,
        data: categoryEntries.map(([, value]) => value),
        itemStyle: { color: '#409eff', borderRadius: [4, 4, 0, 0] },
        label: { show: true, position: 'top' }
      }
    ]
  })
}

function handleResize() {
  statusChart && statusChart.resize()
  priorityChart && priorityChart.resize()
  categoryChart && categoryChart.resize()
}

async function fetchData() {
  loading.value = true
  try {
    const resp = await getDashboard()
    dashboard.value = resp.data
    dataLoaded.value = true
    await Promise.resolve()
    renderCharts()
  } catch {
    dataLoaded.value = false
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  fetchData()
  window.addEventListener('resize', handleResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  statusChart && statusChart.dispose()
  priorityChart && priorityChart.dispose()
  categoryChart && categoryChart.dispose()
  statusChart = priorityChart = categoryChart = null
})
</script>

<style scoped>
.page {
  height: 100%;
}

.stat-card {
  text-align: center;
  padding: 6px 0;
}

.stat-value {
  font-size: 30px;
  font-weight: 600;
  line-height: 1.3;
}

.stat-label {
  margin-top: 4px;
  font-size: 13px;
  color: #909399;
}

.chart-row {
  margin-top: 16px;
}

.chart-card {
  margin-bottom: 16px;
}

/* echarts 容器必须固定高度 */
.chart-box {
  height: 300px;
  width: 100%;
}
</style>
