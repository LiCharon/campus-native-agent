import client from './client'

// 会话 API（M5-ZJUT 服务端化）：/api/sessions 增删改查 + 消息历史
// 会话结构（服务端 SessionItem）：{ id, thread_id, title, title_source, handoff, created_at, updated_at }

// GET /api/sessions → { items: SessionItem[] }（按 updated_at 降序，不含消息）
export function listSessions() {
  return client.get('/sessions')
}

// POST /api/sessions → SessionItem（服务端生成 id/thread_id，title="新对话"）
export function createSession() {
  return client.post('/sessions')
}

// PATCH /api/sessions/{id} body {"title"?, "handoff"?} → SessionItem
export function updateSession(id, data) {
  return client.patch(`/sessions/${id}`, data)
}

// DELETE /api/sessions/{id} → { ok: true }（消息级联删除）
export function deleteSession(id) {
  return client.delete(`/sessions/${id}`)
}

// GET /api/sessions/{id}/messages → { items: MessageItem[] }（按 id 升序）
// MessageItem: { id, role, content, route, outcome, pending_question, tool_calls, status_events, sources, error, feedback_submitted, created_at }
export function listMessages(id) {
  return client.get(`/sessions/${id}/messages`)
}
