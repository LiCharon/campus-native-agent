import client from './client'

// 管理员特权接口（M4）：知识库浏览 / 数据看板 / 用户管理 / 日志管理
// 后端分批接入；未接入前调用会 404/500，页面已做空态兜底

export function fetchKnowledge(params) {
  return client.get('/admin/knowledge', { params })
}

// M9 知识条目增改删（kb_review）
export function createKnowledge(payload) {
  return client.post('/admin/knowledge', payload)
}

export function updateKnowledge(id, payload) {
  return client.put(`/admin/knowledge/${id}`, payload)
}

export function deleteKnowledge(id) {
  return client.delete(`/admin/knowledge/${id}`)
}

export function fetchStats() {
  return client.get('/admin/stats')
}

export function fetchUsers() {
  return client.get('/admin/users')
}

// M6 RBAC：角色/权限下拉数据源（查库，替代前端硬编码枚举）
export function fetchRoles() {
  return client.get('/admin/roles')
}

export function fetchPermissions() {
  return client.get('/admin/permissions')
}

export function createUser(payload) {
  return client.post('/admin/users', payload)
}

export function updateUser(id, payload) {
  return client.put(`/admin/users/${id}`, payload)
}

export function resetPassword(id, payload) {
  return client.post(`/admin/users/${id}/reset-password`, payload)
}

export function fetchLogs(params) {
  return client.get('/admin/logs', { params })
}
