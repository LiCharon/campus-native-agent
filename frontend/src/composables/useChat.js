import { ref } from 'vue'

// 转人工状态三态（§7 状态机）：none=在线咨询 / transferring=转人工处理中 / human=人工客服已接入
// 真实客服后端未接入，human 由前端在转人工后模拟约 3 秒推进，纯前端态、仅本地持久化。
export const HANDOFF = { NONE: 'none', TRANSFERRING: 'transferring', HUMAN: 'human' }

// 会话管理：会话列表与消息全部存 localStorage，刷新页面可恢复。
// 后端硬约束：thread_id 决定对话上下文，新对话必须生成新 thread_id。
// 会话结构：{ id, thread_id, title, createdAt, messages: [{role, content, route?, pendingQuestion?, pending?, error?}] }

// key 按登录用户隔离（同一浏览器多账号互不串）；未登录 guest 兜底
function storageKeys() {
  let uid = 'guest'
  try {
    uid = JSON.parse(localStorage.getItem('cd_user') || '{}').id || 'guest'
  } catch {
    /* 保持 guest */
  }
  return { conv: `cd_conversations_${uid}`, current: `cd_current_${uid}` }
}

const conversations = ref([])
const currentId = ref(null)

function load() {
  const { conv, current } = storageKeys()
  try {
    const raw = localStorage.getItem(conv)
    conversations.value = raw ? JSON.parse(raw) : []
  } catch {
    conversations.value = []
  }
  currentId.value = localStorage.getItem(current)
  // 若保存的当前会话已被清掉，落到第一条
  if (currentId.value && !findConv(currentId.value)) {
    currentId.value = conversations.value.length ? conversations.value[0].id : null
  }
}

function persist() {
  const { conv, current } = storageKeys()
  localStorage.setItem(conv, JSON.stringify(conversations.value))
  if (currentId.value) {
    localStorage.setItem(current, currentId.value)
  } else {
    localStorage.removeItem(current)
  }
}

// 切换登录用户后重新加载会话（Chat.vue 挂载时调用——模块级状态在页面
// 首次加载时只 load 一次，换账号不重新加载会残留上一账号的会话列表）
export function reload() {
  load()
}

function findConv(id) {
  return conversations.value.find((c) => c.id === id) || null
}

// 新建会话：thread_id 用 crypto.randomUUID()（浏览器原生，保证每次新对话唯一）
// M4（Kimi §5.2）：title 占位"新对话"，首条消息后自动取前 12 字；titleSource 标记手动/自动
export function newConversation() {
  const conv = {
    id: crypto.randomUUID(),
    thread_id: crypto.randomUUID(),
    title: '新对话',
    titleSource: 'auto',
    handoff: HANDOFF.NONE,
    createdAt: Date.now(),
    messages: []
  }
  conversations.value.unshift(conv)
  currentId.value = conv.id
  persist()
  return conv
}

// M4：手动重命名（titleSource=manual 后自动生成不再覆盖）
export function renameConversation(id, title) {
  const conv = findConv(id)
  if (!conv) return
  const t = (title || '').trim()
  conv.title = t || '新对话'
  conv.titleSource = t ? 'manual' : 'auto'
  persist()
}

// M4：自动生成主题（首条用户消息前 12 字；手动重命名后不覆盖）
function autoTitleIfNeeded(conv, msg) {
  if (conv.titleSource === 'manual' || msg.role !== 'user' || !msg.content) return
  if (conv.title !== '新对话' && conv.messages.length > 0) return
  const raw = msg.content.replace(/\s+/g, '')
  conv.title = raw.length > 12 ? `${raw.slice(0, 12)}…` : raw
}

export function switchConversation(id) {
  if (findConv(id)) {
    currentId.value = id
    persist()
  }
}

export function deleteConversation(id) {
  const idx = conversations.value.findIndex((c) => c.id === id)
  if (idx === -1) return
  conversations.value.splice(idx, 1)
  if (currentId.value === id) {
    currentId.value = conversations.value.length ? conversations.value[0].id : null
  }
  persist()
}

// 登出时清空内存会话态（用户隔离：防残留上一账号列表）
export function clearConversations() {
  conversations.value = []
  currentId.value = null
}

// 向当前会话追加消息（role: 'user' | 'assistant'）
export function addMessage(msg) {
  const conv = findConv(currentId.value)
  if (!conv) return null
  autoTitleIfNeeded(conv, msg)
  conv.messages.push(msg)
  persist()
  return msg
}

// 替换当前会话最后一条 assistant 消息（发送中占位 → 真实回复）
export function replaceLastMessage(msg) {
  const conv = findConv(currentId.value)
  if (!conv || conv.messages.length === 0) return
  conv.messages[conv.messages.length - 1] = msg
  persist()
}

// 指定会话追加消息（发送期间用户切走会话时，回复仍落在原会话）
export function addMessageTo(convId, msg) {
  const conv = findConv(convId)
  if (!conv) return null
  autoTitleIfNeeded(conv, msg)
  conv.messages.push(msg)
  persist()
  return msg
}

// 指定会话替换最后一条消息
export function replaceLastIn(convId, msg) {
  const conv = findConv(convId)
  if (!conv || conv.messages.length === 0) return
  conv.messages[conv.messages.length - 1] = msg
  persist()
}

// 设置会话转人工状态（none/transferring/human），仅本地持久化
export function setHandoff(id, state) {
  const conv = findConv(id)
  if (!conv) return
  conv.handoff = state
  persist()
}

export function currentConversation() {
  return findConv(currentId.value)
}

load()

export function useChat() {
  return {
    conversations,
    currentId,
    currentConversation,
    newConversation,
    switchConversation,
    deleteConversation,
    clearConversations,
    renameConversation,
    addMessage,
    replaceLastMessage,
    addMessageTo,
    replaceLastIn,
    setHandoff,
    HANDOFF,
    reload
  }
}
