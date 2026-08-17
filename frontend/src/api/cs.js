import client from './client'

// 客服工作台（M4）：待接待队列 + 标记已处理

export function fetchQueue() {
  return client.get('/cs/queue')
}

export function resolveCase(id) {
  return client.post(`/cs/${id}/resolve`)
}
