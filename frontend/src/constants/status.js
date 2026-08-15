// 前端自持的中文映射（M1-ZJUT 收敛版）：报修/投诉/工单退役后仅保留仍被引用的导出
// - ROLE_CN：App.vue 角色中文映射（仍引用）
// - routeLabel / ROUTE_META：Chat.vue 消息 route 字段标记（仍引用；键为旧版
//   报修/咨询/投诉路由，M1 实际 route=knowledge/tool_query/multi_intent/human_handoff，
//   未命中键原样兜底，暂不影响展示）
// 已删：PRIORITY_META、priorityMeta、TERMINAL_STATUS（无引用）
// 已无引用（M1-T10 删工单卡片后遗留，M2 工作台如需再启用）：STATUS_META、
//   statusMeta、TICKET_TYPE_META、typeMeta

// 工单状态 → 中文/标签类型（已无引用：工单卡片 M1-T10 退役；M2 工作台如需再启用）
export const STATUS_META = {
  SUBMITTED: { label: '待派单', type: 'info' },
  ASSIGNED: { label: '已派单', type: 'warning' },
  IN_PROGRESS: { label: '维修中', type: 'primary' },
  PENDING_VERIFY: { label: '待验收', type: 'warning' },
  CLOSED: { label: '已完成', type: 'success' },
  CANCELLED: { label: '已取消', type: 'info' }
}

export const ROLE_CN = {
  student: '学生',
  cs_staff: '客服',
  staff: '后勤员工',
  it_staff: '信息中心员工',
  admin: '管理员'
}

// 工单类型（ticket_type 代码值 → 中文；已无引用，同上）
export const TICKET_TYPE_META = {
  repair: { label: '报修', type: 'primary' },
  complaint: { label: '投诉', type: 'danger' }
}

// Agent 对话路由（reply 里的 route 字段；M1 实际值 knowledge/tool_query/
// multi_intent/human_handoff，未列出的键由 routeLabel 原样兜底）
export const ROUTE_META = {
  repair: '报修',
  consult: '咨询',
  complaint: '投诉',
  human_handoff: '人工转接',
  quality: '质检'
}

export function statusMeta(code) {
  return STATUS_META[code] || { label: code || '未知', type: 'info' }
}

export function typeMeta(code) {
  return TICKET_TYPE_META[code] || { label: code || '未知', type: 'info' }
}

export function routeLabel(code) {
  return ROUTE_META[code] || code || ''
}
