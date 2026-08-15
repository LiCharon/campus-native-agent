<template>
  <div class="chat-layout">
    <!-- 左侧会话列表 -->
    <div class="conv-panel">
      <div class="conv-actions">
        <el-button
          type="primary"
          :icon="Plus"
          class="new-btn"
          @click="handleNewConversation"
        >
          新对话
        </el-button>
      </div>
      <div class="conv-list">
        <el-empty
          v-if="conversations.length === 0"
          :image-size="60"
          description="暂无会话"
        />
        <div
          v-for="conv in conversations"
          :key="conv.id"
          class="conv-item"
          :class="{ active: conv.id === currentId }"
          @click="handleSwitch(conv.id)"
        >
          <div class="conv-item-main">
            <div class="conv-title">{{ conv.title }}</div>
            <div class="conv-meta">{{ formatTime(conv.createdAt) }} · {{ conv.messages.length }} 条</div>
          </div>
          <el-icon
            class="conv-del"
            title="删除会话"
            @click.stop="handleDelete(conv.id)"
          >
            <Delete />
          </el-icon>
        </div>
      </div>
    </div>

    <!-- 右侧消息区 -->
    <div class="msg-panel">
      <div ref="msgListRef" class="msg-list">
        <div v-if="!current || current.messages.length === 0" class="msg-welcome">
          <el-icon :size="40" color="#c0c4cc"><ChatDotRound /></el-icon>
          <p>你好，我是 Campus Native Agent 校园服务助理</p>
          <p class="welcome-sub">
            可以直接描述遇到的问题，例如：<br />
            “图书馆几点开门？” / “一卡通怎么补办？” / “校历什么时候出？”
          </p>
        </div>

        <div
          v-for="(msg, idx) in currentMessages"
          :key="idx"
          class="msg-row"
          :class="msg.role"
        >
          <div class="msg-avatar" :class="msg.role">
            <el-icon><User v-if="msg.role === 'user'" /><Cpu v-else /></el-icon>
          </div>
          <div class="msg-body">
            <div class="msg-bubble" :class="[msg.role, { error: msg.error }]">
              {{ msg.content }}
            </div>

            <!-- 等待补充提示 -->
            <div v-if="msg.pendingQuestion" class="pending-tip">
              <el-icon><QuestionFilled /></el-icon>
              <span>等待补充：{{ msg.pendingQuestion }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- 底部输入区 -->
      <div class="input-area">
        <el-input
          v-model="draft"
          type="textarea"
          :rows="2"
          resize="none"
          :disabled="sending"
          :placeholder="sending ? 'Agent 思考中…' : '输入消息，Enter 发送（Shift+Enter 换行）'"
          @keydown.enter.exact.prevent="handleSend"
        />
        <div class="input-actions">
          <span v-if="sending" class="thinking-tip">
            <el-icon class="is-loading"><Loading /></el-icon>
            Agent 思考中…
          </span>
          <span v-else />
          <el-button type="primary" :loading="sending" @click="handleSend">
            发 送
          </el-button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Plus,
  Delete,
  User,
  Cpu,
  ChatDotRound,
  QuestionFilled,
  Loading
} from '@element-plus/icons-vue'
import { sendChat } from '../api/chat'
import { routeLabel } from '../constants/status'
import { useChat } from '../composables/useChat'

const {
  conversations,
  currentId,
  newConversation,
  switchConversation,
  deleteConversation,
  addMessageTo,
  replaceLastIn,
  reload
} = useChat()

// 切换账号后会话列表仍残留上一账号（模块级状态只 load 一次）——
// 每次进入对话页强制按当前登录用户重载（验收抓出）
reload()

const draft = ref('')
const sending = ref(false)
const msgListRef = ref(null)

const current = computed(() => conversations.value.find((c) => c.id === currentId.value) || null)
const currentMessages = computed(() => (current.value ? current.value.messages : []))

function handleNewConversation() {
  if (sending.value) {
    ElMessage.warning('Agent 正在处理中，请稍候')
    return
  }
  newConversation()
}

function handleSwitch(id) {
  if (sending.value) {
    ElMessage.warning('Agent 正在处理中，请稍候')
    return
  }
  switchConversation(id)
}

async function handleDelete(id) {
  try {
    await ElMessageBox.confirm('确定删除该会话？历史记录不可恢复。', '删除会话', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消'
    })
  } catch {
    return
  }
  deleteConversation(id)
}

function scrollToBottom() {
  nextTick(() => {
    if (msgListRef.value) {
      msgListRef.value.scrollTop = msgListRef.value.scrollHeight
    }
  })
}

async function handleSend() {
  const msg = draft.value.trim()
  if (!msg || sending.value) return
  if (!current.value) return

  const convId = currentId.value
  draft.value = ''

  // 立即追加用户气泡 + Agent 占位，禁用输入框
  addMessageTo(convId, { role: 'user', content: msg })
  addMessageTo(convId, {
    role: 'assistant',
    content: 'Agent 思考中…',
    pending: true
  })
  sending.value = true
  scrollToBottom()

  try {
    const resp = await sendChat(current.value.thread_id, msg)
    const data = resp.data

    replaceLastIn(convId, {
      role: 'assistant',
      content: data.reply || '（无回复）',
      route: routeLabel(data.route),
      pendingQuestion: data.pending_question || '',
      finished: data.finished
    })
  } catch {
    replaceLastIn(convId, {
      role: 'assistant',
      content: '请求失败，请稍后重试。',
      error: true
    })
    ElMessage.error('与服务的通信失败，请检查后端是否启动')
  } finally {
    sending.value = false
    scrollToBottom()
  }
}

function formatTime(ts) {
  return new Date(ts).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  })
}

watch(
  () => (current.value ? current.value.messages.length : 0),
  () => scrollToBottom()
)

// 刷新页面恢复当前会话（useChat 初始化时已从 localStorage 恢复）；
// 首次访问（无任何会话）时自动创建一个新会话
if (!current.value) {
  newConversation()
}
</script>

<style scoped>
.chat-layout {
  display: flex;
  height: 100%;
  gap: 16px;
}

/* 左侧会话列 */
.conv-panel {
  width: 260px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
}

.conv-actions {
  padding: 12px;
  border-bottom: 1px solid #f0f2f5;
}

.new-btn {
  width: 100%;
}

.conv-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
}

.conv-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 10px 12px;
  border-radius: 6px;
  cursor: pointer;
  margin-bottom: 4px;
  transition: background-color 0.15s;
}

.conv-item:hover {
  background: #f5f7fa;
}

.conv-item.active {
  background: #ecf5ff;
}

.conv-item-main {
  flex: 1;
  min-width: 0;
}

.conv-title {
  font-size: 13px;
  color: #303133;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.conv-meta {
  font-size: 12px;
  color: #909399;
  margin-top: 2px;
}

.conv-del {
  color: #c0c4cc;
  cursor: pointer;
  visibility: hidden;
}

.conv-item:hover .conv-del {
  visibility: visible;
}

.conv-del:hover {
  color: #f56c6c;
}

/* 右侧消息区 */
.msg-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  min-width: 0;
}

.msg-list {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
  background: #f5f7fa;
}

.msg-welcome {
  text-align: center;
  margin-top: 15%;
  color: #606266;
}

.msg-welcome p {
  margin: 12px 0 4px;
  font-size: 15px;
}

.welcome-sub {
  font-size: 13px !important;
  color: #909399 !important;
  line-height: 1.8;
}

.msg-row {
  display: flex;
  gap: 10px;
  margin-bottom: 18px;
}

.msg-row.user {
  flex-direction: row-reverse;
}

.msg-avatar {
  width: 36px;
  height: 36px;
  flex-shrink: 0;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  font-size: 18px;
}

.msg-avatar.user {
  background: #409eff;
}

.msg-avatar.assistant {
  background: #67c23a;
}

.msg-body {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 6px;
}

.msg-row.user .msg-body {
  align-items: flex-end;
}

.msg-bubble.error {
  color: #f56c6c;
}

.pending-tip {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: #e6a23c;
  background: #fdf6ec;
  border: 1px solid #faecd8;
  border-radius: 4px;
  padding: 4px 10px;
}

.input-area {
  padding: 12px 16px;
  border-top: 1px solid #e4e7ed;
  background: #fff;
  border-radius: 0 0 8px 8px;
}

.input-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 8px;
}

.thinking-tip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #909399;
}
</style>
