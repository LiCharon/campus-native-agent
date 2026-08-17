<template>
  <div class="page">
    <div class="cd-ph">
      <div>
        <h2>数据看板</h2>
        <p>服务运行指标 · 近 14 天反馈趋势</p>
      </div>
      <button class="cd-btn ghost" @click="load">
        <svg viewBox="0 0 24 24" class="ic"><path d="M4 4v6h6M20 20v-6h-6"/><path d="M4 10a8 8 0 0 1 14-3M20 14a8 8 0 0 1-14 3"/></svg>
        刷新
      </button>
    </div>
    <div class="content">
      <div v-if="!loaded" class="cd-empty">数据接入中…</div>
      <template v-else>
        <div class="cards">
          <div class="card"><div class="k">用户数</div><div class="v">{{ stats.user_count }}</div></div>
          <div class="card"><div class="k">知识条目</div><div class="v">{{ stats.knowledge_count }}</div></div>
          <div class="card"><div class="k">待审</div><div class="v">{{ stats.pending_bad_cases + stats.pending_suggestions }}</div></div>
          <div class="card"><div class="k">已采纳</div><div class="v">{{ stats.adopted }}</div></div>
          <div class="card"><div class="k">已驳回</div><div class="v">{{ stats.rejected }}</div></div>
          <div class="card"><div class="k">已处理（转人工）</div><div class="v">{{ stats.resolved }}</div></div>
        </div>
        <div class="grid2">
          <div class="panel"><h3>近 14 天 反馈 / 采纳趋势</h3><div ref="trendRef" class="chart"></div></div>
          <div class="panel"><h3>知识条目类型分布</h3><div ref="donutRef" class="chart"></div></div>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { nextTick } from 'vue'
import * as echarts from 'echarts'
import { fetchStats } from '../api/admin'

const stats = ref({})
const loaded = ref(false)
const trendRef = ref(null)
const donutRef = ref(null)
let trendChart = null
let donutChart = null

const TYPE_LABEL = { info: 'info 知识型', process: 'process 流程型', index: 'index 索引型' }

async function load() {
  try {
    const resp = await fetchStats()
    stats.value = resp.data
    loaded.value = true
    nextTick(() => renderCharts())
  } catch {
    loaded.value = false
  }
}

function renderCharts() {
  if (trendRef.value) {
    trendChart = trendChart || echarts.init(trendRef.value)
    const days = stats.value.feedback_by_day || []
    trendChart.setOption({
      tooltip: { trigger: 'axis' },
      legend: { data: ['反馈', '采纳'], textStyle: { fontSize: 11 } },
      grid: { left: 34, right: 12, top: 28, bottom: 24 },
      xAxis: { type: 'category', data: days.map((d) => d.date.slice(5)), axisLabel: { fontSize: 10 } },
      yAxis: { type: 'value', minInterval: 1, axisLabel: { fontSize: 10 } },
      series: [
        { name: '反馈', type: 'bar', data: days.map((d) => d.bad_case + d.suggestion), itemStyle: { color: '#1f6fc4' }, barWidth: '55%' },
        { name: '采纳', type: 'bar', data: days.map((d) => 0), itemStyle: { color: '#067647' }, barWidth: '55%' }
      ]
    })
  }
  if (donutRef.value) {
    donutChart = donutChart || echarts.init(donutRef.value)
    const dist = stats.value.type_dist || {}
    donutChart.setOption({
      tooltip: { trigger: 'item' },
      series: [{
        type: 'pie',
        radius: ['42%', '68%'],
        itemStyle: { borderRadius: 6, borderColor: '#fff', borderWidth: 2 },
        label: { fontSize: 11 },
        data: Object.entries(dist).map(([k, v]) => ({ name: TYPE_LABEL[k] || k, value: v }))
      }]
    })
  }
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

.cards {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
  margin-bottom: 22px;
}

.card {
  background: #fff;
  border: 1px solid var(--cd-line);
  border-radius: var(--cd-radius-card);
  padding: 20px 22px;
  box-shadow: var(--cd-shadow-sm);
  position: relative;
  overflow: hidden;
}

.card::before {
  content: '';
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 3px;
  background: var(--cd-grad);
  opacity: 0.85;
}

.card .k {
  font-size: 12.5px;
  color: var(--cd-text-3);
  font-weight: 500;
}

.card .v {
  font-size: 30px;
  font-weight: 700;
  margin-top: 8px;
  letter-spacing: -0.5px;
  color: var(--cd-text-1);
}

.grid2 {
  display: grid;
  grid-template-columns: 1.4fr 1fr;
  gap: 18px;
}

.panel {
  background: #fff;
  border: 1px solid var(--cd-line);
  border-radius: var(--cd-radius-card);
  padding: 20px 22px;
  box-shadow: var(--cd-shadow-sm);
}

.panel h3 {
  font-size: 14px;
  font-weight: 700;
  margin: 0 0 14px;
  color: var(--cd-text-1);
}

.chart {
  height: 260px;
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
