// 状态/优先级/角色/类型的中文映射，前端自持（后端只回传代码值）

export const STATUS_META = {
  SUBMITTED: { label: '待派单', type: 'info' },
  ASSIGNED: { label: '已派单', type: 'warning' },
  IN_PROGRESS: { label: '维修中', type: 'primary' },
  PENDING_VERIFY: { label: '待验收', type: 'warning' },
  CLOSED: { label: '已完成', type: 'success' },
  CANCELLED: { label: '已取消', type: 'info' }
}

export const PRIORITY_META = {
  P1: { label: '紧急', type: 'danger' },
  P2: { label: '普通', type: 'primary' },
  P3: { label: '预约', type: 'info' }
}

export const ROLE_CN = {
  student: '学生',
  staff: '后勤员工',
  it_staff: '信息中心员工',
  admin: '管理员'
}

// 工单类型（ticket_type 代码值 → 中文；后端返回小写 repair/complaint；未知值原样兜底）
export const TICKET_TYPE_META = {
  repair: { label: '报修', type: 'primary' },
  complaint: { label: '投诉', type: 'danger' }
}

// Agent 对话路由（reply 里的 route 字段）
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

export function priorityMeta(code) {
  return PRIORITY_META[code] || { label: code || '-', type: 'info' }
}

export function typeMeta(code) {
  return TICKET_TYPE_META[code] || { label: code || '未知', type: 'info' }
}

export function routeLabel(code) {
  return ROUTE_META[code] || code || ''
}

// 非终态（可操作 验收/撤回）的状态集合
export const TERMINAL_STATUS = ['CLOSED', 'CANCELLED']
