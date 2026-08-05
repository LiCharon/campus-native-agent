import client from './client'

// GET /api/tickets?status=&limit=20&offset=0 → {"items":[TicketSummary],"total":N}
export function getTickets(params = {}) {
  return client.get('/tickets', { params })
}

// GET /api/tickets/{id} → TicketDetail（越权与不存在均为 404）
export function getTicket(id) {
  return client.get(`/tickets/${id}`)
}

// POST /api/tickets/{id}/verify 工单 owner 验收
export function verifyTicket(id) {
  return client.post(`/tickets/${id}/verify`)
}

// POST /api/tickets/{id}/cancel 工单 owner 撤回
export function cancelTicket(id) {
  return client.post(`/tickets/${id}/cancel`)
}

// GET /api/dashboard → {"total","by_status","by_priority","by_category"}（staff+ 才可访问）
export function getDashboard() {
  return client.get('/dashboard')
}

// POST /api/admin/tickets/{id}/assign  body {"repairman_id","dept"}
export function assignTicket(id, payload) {
  return client.post(`/admin/tickets/${id}/assign`, payload)
}

// GET /api/admin/staff → [{"id","name","dept","trade"}] 维修人下拉
export function getStaff() {
  return client.get('/admin/staff')
}
