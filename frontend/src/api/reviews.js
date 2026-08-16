import client from './client'

// 进化闭环（M3）：学生反馈双通道 + 管理员审查

// ① 对话页"没解决"手动反馈（写 bad_cases）
export function submitBadCase(payload) {
  return client.post('/feedback/bad-case', payload)
}

// ② 对话页"问题没答案"提议（写 suggestions）
export function submitSuggestion(payload) {
  return client.post('/feedback/suggestion', payload)
}

// 待审列表（kind: bad_cases | suggestions）
export function fetchReviews(kind, status = 'PENDING') {
  return client.get('/admin/reviews', { params: { kind, status } })
}

// 补入知识库（来源状态流转：bad_cases→RESOLVED / suggestions→ADOPTED）
export function adoptReview(kind, id, payload) {
  return client.post(`/admin/reviews/${kind}/${id}/adopt`, payload)
}

// 驳回（bad_cases→RESOLVED / suggestions→REJECTED，不补入）
export function dismissReview(kind, id) {
  return client.post(`/admin/reviews/${kind}/${id}/dismiss`)
}
