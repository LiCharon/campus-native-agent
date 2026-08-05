import { ref } from 'vue'

// 会话管理：会话列表与消息全部存 localStorage，刷新页面可恢复。
// 后端硬约束：thread_id 决定对话上下文，新对话必须生成新 thread_id。
// 会话结构：{ id, thread_id, title, createdAt, messages: [{role, content, ticket?, route?, pendingQuestion?, pending?, error?}] }

const STORAGE_KEY = 'cd_conversations'
const CURRENT_KEY = 'cd_current_conversation'

const conversations = ref([])
const currentId = ref(null)

function load() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    conversations.value = raw ? JSON.parse(raw) : []
  } catch {
    conversations.value = []
  }
  currentId.value = localStorage.getItem(CURRENT_KEY)
  // 若保存的当前会话已被清掉，落到第一条
  if (currentId.value && !findConv(currentId.value)) {
    currentId.value = conversations.value.length ? conversations.value[0].id : null
  }
}

function persist() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(conversations.value))
  if (currentId.value) {
    localStorage.setItem(CURRENT_KEY, currentId.value)
  } else {
    localStorage.removeItem(CURRENT_KEY)
  }
}

function findConv(id) {
  return conversations.value.find((c) => c.id === id) || null
}

// 新建会话：thread_id 用 crypto.randomUUID()（浏览器原生，保证每次新对话唯一）
export function newConversation() {
  const conv = {
    id: crypto.randomUUID(),
    thread_id: crypto.randomUUID(),
    title: `对话 ${new Date().toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })}`,
    createdAt: Date.now(),
    messages: []
  }
  conversations.value.unshift(conv)
  currentId.value = conv.id
  persist()
  return conv
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

// 向当前会话追加消息（role: 'user' | 'assistant'）
export function addMessage(msg) {
  const conv = findConv(currentId.value)
  if (!conv) return null
  // 首条消息作为会话标题
  if (conv.messages.length === 0 && msg.role === 'user' && msg.content) {
    conv.title = msg.content.length > 16 ? `${msg.content.slice(0, 16)}…` : msg.content
  }
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
  if (conv.messages.length === 0 && msg.role === 'user' && msg.content) {
    conv.title = msg.content.length > 16 ? `${msg.content.slice(0, 16)}…` : msg.content
  }
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
    addMessage,
    replaceLastMessage,
    addMessageTo,
    replaceLastIn
  }
}
