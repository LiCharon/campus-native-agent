import client from './client'

// POST /api/chat  body {"thread_id","msg"}  header Authorization: Bearer <token>
// → {"reply","route","pending_question","ticket_id","ticket_status",
//    "finished","tool_calls","status_events","outcome"}（outcome 可能不存在）
export function sendChat(threadId, msg) {
  return client.post('/chat', { thread_id: threadId, msg })
}
