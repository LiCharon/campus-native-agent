import client from './client'

// 管理员特权接口（M4）：知识库浏览 / 数据看板 / 用户管理 / 日志管理
// 后端分批接入；未接入前调用会 404/500，页面已做空态兜底

export function fetchKnowledge(params) {
  return client.get('/admin/knowledge', { params })
}

export function fetchStats() {
  return client.get('/admin/stats')
}

export function fetchUsers() {
  return client.get('/admin/users')
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
