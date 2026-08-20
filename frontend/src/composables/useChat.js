import { ref } from 'vue'
import {
  listSessions,
  createSession,
  updateSession,
  deleteSession,
  listMessages
} from '../api/sessions'

// 转人工状态三态（§7 状态机）：none=在线咨询 / transferring=转人工处理中 / human=人工客服已接入
// M5-ZJUT 起 handoff 落库（服务端 conversations.handoff）；3 秒模拟仍由前端推进
// （setTimeout 仅推进状态走 API），刷新停在中间态不再自动推进——已确认接受。
export const HANDOFF = { NONE: 'none', TRANSFERRING: 'transferring', HUMAN: 'human' }

// 会话管理（M5-ZJUT 服务端化）：会话列表与消息全部由后端 MySQL 管理，
// 前端只保留内存态 + currentId 的 UI 偏好（localStorage 仅记"上次选中哪个会话"，
// 刷新后若该会话仍存在则选中，否则回落第一条）。旧 localStorage 会话数据不迁移（用户拍板）。

// currentId 的 localStorage key 按用户隔离（多账号同浏览器互不串）
function currentKey() {
  let uid = 'guest'
  try {
    uid = JSON.parse(localStorage.getItem('cd_user') || '{}').id || 'guest'
  } catch {
    /* 保持 guest */
  }
  return `cd_current_${uid}`
}

const conversations = ref([]) // 会话列表（服务端 SessionItem，不含消息）
const currentId = ref(null) // 当前会话 id（UI 偏好，可持久化）
const currentMessages = ref([]) // 当前会话消息（内存态；落库由后端 chat 完成）

// 消息拉取竞态防护（grill Q2）：快速切换会话时，旧响应到达后校验
// 目标会话仍是当前会话，否则丢弃（避免乱序覆盖）
let msgSeq = 0

function findConv(id) {
  return conversations.value.find((c) => c.id === id) || null
}

function persistCurrent() {
  if (currentId.value) {
    localStorage.setItem(currentKey(), currentId.value)
  } else {
    localStorage.removeItem(currentKey())
  }
}

async function loadCurrentMessages() {
  const cid = currentId.value
  if (!cid) {
    currentMessages.value = []
    return
  }
  const seq = ++msgSeq
  try {
    const { data } = await listMessages(cid)
    if (seq !== msgSeq || currentId.value !== cid) return // 过期响应丢弃
    currentMessages.value = data.items || []
  } catch {
    /* 拉取失败保持现状（可能已被切换） */
  }
}

// 加载会话列表（刷新/切换账号时调用）；恢复 UI 偏好的当前会话并拉消息
async function load() {
  try {
    const { data } = await listSessions()
    conversations.value = data.items || []
  } catch {
    conversations.value = []
  }
  const saved = localStorage.getItem(currentKey())
  if (saved && findConv(saved)) {
    currentId.value = saved
  } else if (conversations.value.length) {
    currentId.value = conversations.value[0].id
  } else {
    currentId.value = null
  }
  await loadCurrentMessages()
}

export function reload() {
  return load()
}

// 新建会话：服务端生成 id/thread_id（会话生命周期归后端）
export async function newConversation() {
  const { data } = await createSession()
  conversations.value.unshift(data) // 新会话 updated_at 最新 → 列表最前
  currentId.value = data.id
  currentMessages.value = []
  persistCurrent()
  return data
}

// 切换会话：设当前 id + 拉取消息历史（每次切换拉最新，发送中切走再切回可见新回复）
export async function switchConversation(id) {
  if (!findConv(id)) return
  currentId.value = id
  persistCurrent()
  await loadCurrentMessages()
}

// 手动重命名（title 置 manual 后自动标题不再覆盖——服务端保证）
export async function renameConversation(id, title) {
  const conv = findConv(id)
  if (!conv) return
  const t = (title || '').trim()
  try {
    const { data } = await updateSession(id, { title: t || '新对话' })
    conv.title = data.title
    conv.title_source = data.title_source
    conv.updated_at = data.updated_at
  } catch {
    /* 服务端失败保持本地现状 */
  }
}

export async function deleteConversation(id) {
  try {
    await deleteSession(id) // 消息级联删除（服务端）
  } catch {
    return
  }
  const idx = conversations.value.findIndex((c) => c.id === id)
  if (idx !== -1) conversations.value.splice(idx, 1)
  if (currentId.value === id) {
    currentId.value = conversations.value.length ? conversations.value[0].id : null
    persistCurrent()
    await loadCurrentMessages()
  }
}

// 登出时清空内存会话态（用户隔离：防残留上一账号列表）
export function clearConversations() {
  conversations.value = []
  currentId.value = null
  currentMessages.value = []
  localStorage.removeItem(currentKey())
}

// ---- 消息（内存态）：即时显示，落库由后端 POST /api/chat 完成 ----
// 发送期间切走会话时：目标会话非当前 → 不写入当前显示（回复仍由后端落库，
// 切回时 loadCurrentMessages 拉到）

export function addMessageTo(convId, msg) {
  if (convId !== currentId.value) return null
  currentMessages.value.push(msg)
  return msg
}

export function replaceLastIn(convId, msg) {
  if (convId !== currentId.value || currentMessages.value.length === 0) return
  currentMessages.value[currentMessages.value.length - 1] = msg
}

// 设置会话转人工状态（走 API 落库）
export async function setHandoff(id, state) {
  const conv = findConv(id)
  if (!conv) return
  try {
    const { data } = await updateSession(id, { handoff: state })
    conv.handoff = data.handoff
  } catch {
    /* 忽略：状态下次刷新恢复 */
  }
}

export function currentConversation() {
  return findConv(currentId.value)
}

export function useChat() {
  return {
    conversations,
    currentId,
    currentMessages,
    currentConversation,
    newConversation,
    switchConversation,
    deleteConversation,
    clearConversations,
    renameConversation,
    addMessageTo,
    replaceLastIn,
    setHandoff,
    HANDOFF,
    reload
  }
}
