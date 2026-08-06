import client from './client'

// FAQ 接口（M7）：读 = 登录即可；写 = 仅 admin（403 由后端 RBAC 门控）

// GET /api/faqs → {"items":[{"id","category","keywords","question","answer"}],"total":N}
export function getFaqs() {
  return client.get('/faqs')
}

// POST /api/admin/faqs（admin）body {"category","keywords","question","answer"}
export function createFaq(payload) {
  return client.post('/admin/faqs', payload)
}

// PUT /api/admin/faqs/{id}（admin）全量更新
export function updateFaq(id, payload) {
  return client.put(`/admin/faqs/${id}`, payload)
}

// DELETE /api/admin/faqs/{id}（admin）
export function deleteFaq(id) {
  return client.delete(`/admin/faqs/${id}`)
}
