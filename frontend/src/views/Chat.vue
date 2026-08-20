<template>
  <div class="chat-page">
    <!-- 顶部：会话主题 + 状态徽标（Kimi §5.7） -->
    <div class="chat-head">
      <span class="ttl">{{ currentConv ? currentConv.title : '校园咨询' }}</span>
      <span class="sub">· 在线办事问答</span>
      <span class="status" :class="handoffClass">
        <span class="dot"></span>{{ handoffLabel }}
      </span>
    </div>

    <!-- 消息区 -->
    <div ref="msgListRef" class="chat-body">
      <div class="chat-inner">
        <div v-if="!currentMessages.length" class="welcome">
          <div class="welcome-icon">
            <svg viewBox="0 0 24 24"><path d="M3 10 12 4l9 6"/><path d="M5 9v10h14V9"/><path d="M9 19v-6h6v6"/><path d="M3 19h18"/></svg>
          </div>
          <h2>你好，我是 {{ BRAND.chatName }}</h2>
          <p>校园服务助理 · 查流程、找入口、问时间，一句话开问<br />例如：今天 1 号楼有空教室吗？</p>
        </div>

        <template v-for="(msg, idx) in currentMessages" :key="idx">
          <!-- 系统提示条（转人工） -->
          <div v-if="msg.role === 'system'" class="sys-note">
            <svg viewBox="0 0 24 24" class="ic"><path d="M4 13a8 8 0 0 1 16 0"/><rect x="3" y="13" width="4" height="7" rx="2"/><rect x="17" y="13" width="4" height="7" rx="2"/></svg>
            <span>{{ msg.content }}</span>
          </div>

          <div v-else class="msg-row" :class="msg.role">
            <div class="bubble" :class="[msg.role, { error: msg.error }]">
              <span v-if="msg.pending" class="thinking">
                <span class="thinking-dot"></span><span class="thinking-dot"></span><span class="thinking-dot"></span>
              </span>
              <template v-else>
                {{ msg.content }}
                <!-- 来源 chip（Kimi §3.3 / §5.4） -->
                <div v-if="msg.sources && msg.sources.length" class="src">
                  <span
                    v-for="(s, i) in msg.sources"
                    :key="i"
                    class="chip"
                    :class="s.type"
                  >
                    <svg v-if="s.type === 'tool'" viewBox="0 0 24 24" class="chip-ic"><circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/></svg>
                    <svg v-else viewBox="0 0 24 24" class="chip-ic"><path d="M12 6.5C10.5 5 7.5 4.5 5 5v12c2.5-.5 5.5 0 7 1.5 1.5-1.5 4.5-2 7-1.5V5c-2.5-.5-5.5 0-7 1.5Z"/><path d="M12 6.5v12"/></svg>
                    {{ s.label }}
                  </span>
                  <span class="src-detail">{{ srcDetail(msg.sources) }}</span>
                </div>
                <!-- 追问提示 -->
                <div v-if="msg.pendingQuestion" class="pending-tip">
                  <svg viewBox="0 0 24 24" class="ic"><circle cx="12" cy="12" r="8.5"/><path d="M12 8.5V12l2.5 1.8"/></svg>
                  <span>等待补充：{{ msg.pendingQuestion }}</span>
                </div>
                <!-- 反馈（转人工自动沉淀的不重复显示） -->
                <span
                  v-if="msg.role === 'assistant' && canFeedback(msg)"
                  class="fb"
                  :class="{ done: msg.feedbackSubmitted }"
                  @click="handleFeedback(msg, idx)"
                >
                  {{ msg.feedbackSubmitted ? '已反馈' : '没解决？' }}
                </span>
              </template>
            </div>
          </div>
        </template>
      </div>
    </div>

    <!-- 快捷问题（Kimi §5.5） -->
    <div class="quick">
      <span class="qlabel">
        <svg viewBox="0 0 24 24" class="ic"><path d="M12 2l1.8 5.2L19 9l-5.2 1.8L12 16l-1.8-5.2L5 9l5.2-1.8Z"/></svg>
        猜你想问
      </span>
      <span v-for="q in quickQuestions" :key="q" class="qchip" @click="send(q)">{{ q }}</span>
    </div>

    <!-- 操作行 + 输入框（Kimi §3.3） -->
    <div class="composer">
      <div class="actions">
        <span class="ghost-btn" @click="handleHandoff">
          <svg viewBox="0 0 24 24" class="ic"><path d="M4 13a8 8 0 0 1 16 0"/><rect x="3" y="13" width="4" height="7" rx="2"/><rect x="17" y="13" width="4" height="7" rx="2"/></svg>
          没解决？转人工
        </span>
        <span class="ghost-btn" @click="handleSuggest">
          <svg viewBox="0 0 24 24" class="ic"><path d="M9 18h6M10 21h4"/><path d="M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2V17h5v-1.1c0-.8.4-1.5 1-2A6 6 0 0 0 12 3Z"/></svg>
          提建议
        </span>
        <span class="ghost-btn warn" @click="handleClear">
          <svg viewBox="0 0 24 24" class="ic"><path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg>
          删除会话
        </span>
      </div>
      <div class="box">
        <textarea
          v-model="draft"
          ref="taRef"
          rows="1"
          placeholder="问任何校园问题，例如：今天 1 号楼有空教室吗？"
          :disabled="sending"
          @keydown.enter.exact.prevent="handleSend"
          @keydown.enter.shift.exact.prevent="insertNewline"
        ></textarea>
        <button class="send" :disabled="sending || !draft.trim()" @click="handleSend">
          <svg viewBox="0 0 24 24"><path d="M22 2 11 13"/><path d="M22 2 15 22l-4-9-9-4Z"/></svg>
        </button>
      </div>
      <div class="tip">{{ BRAND.tipPrefix }} · 回复内容由工具与知识库提供，仅供办事参考</div>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { sendChat } from '../api/chat'
import { submitBadCase, submitSuggestion } from '../api/reviews'
import { useChat, HANDOFF, setHandoff } from '../composables/useChat'
import { BRAND } from '../config/brand'

const QUICK_QUESTIONS = [
  '怎么申请教室借用？',
  '奖学金什么时候评定？',
  '校园网怎么充值？',
  '本科生能申请保研吗？'
]

const {
  conversations,
  currentId,
  currentMessages,
  newConversation,
  deleteConversation,
  addMessageTo,
  replaceLastIn
} = useChat()

const currentConv = computed(() => conversations.value.find((c) => c.id === currentId.value) || null)
// 三态：none=在线咨询(绿) / transferring|pending=转人工处理中(琥珀) / human|active=人工客服已接入(蓝)
const handoffState = computed(() => (currentConv.value && currentConv.value.handoff) || HANDOFF.NONE)
const isHandoffBusy = computed(() => handoffState.value === 'transferring' || handoffState.value === 'pending')
const isHandoffHuman = computed(() => handoffState.value === 'human' || handoffState.value === 'active')
const handoffLabel = computed(() =>
  isHandoffHuman.value ? '人工客服已接入' : isHandoffBusy.value ? '转人工处理中' : '在线咨询'
)
const handoffClass = computed(() => (isHandoffHuman.value ? 'human' : isHandoffBusy.value ? 'busy' : ''))

const draft = ref('')
const sending = ref(false)
const msgListRef = ref(null)
const taRef = ref(null)
const quickQuestions = QUICK_QUESTIONS

function scrollToBottom() {
  nextTick(() => {
    if (msgListRef.value) {
      msgListRef.value.scrollTop = msgListRef.value.scrollHeight
    }
  })
}

function autoGrow() {
  const ta = taRef.value
  if (!ta) return
  ta.style.height = 'auto'
  // 空草稿（只剩 placeholder）→ 强制单行；否则用 scrollHeight，但不超过 120px
  const isEmpty = !draft.value || !draft.value.trim()
  const h = isEmpty ? 24 : ta.scrollHeight
  ta.style.height = Math.min(Math.max(h, 24), 120) + 'px'
}

function insertNewline() {
  const ta = taRef.value
  if (ta) {
    const start = ta.selectionStart
    const end = ta.selectionEnd
    draft.value = draft.value.slice(0, start) + '\n' + draft.value.slice(end)
    nextTick(() => {
      ta.selectionStart = ta.selectionEnd = start + 1
      autoGrow()
    })
  }
}

function canFeedback(msg) {
  return msg.outcome !== 'handoff' && !msg.feedbackSubmitted && !msg.pending && !msg.error
}

function srcDetail(sources) {
  const kb = sources.filter((s) => s.type === 'kb')
  if (kb.length) return `来源：${kb[0].refId} ${kb[0].detail}`
  const t = sources.find((s) => s.type === 'tool')
  return t ? `来源：${t.detail}` : ''
}

async function send(text) {
  const msg = (text ?? draft.value).trim()
  if (!msg || sending.value) return
  if (!currentConv.value) await newConversation() // M5-ZJUT：先建会话拿 thread_id
  const convId = currentId.value
  draft.value = ''
  autoGrow()

  addMessageTo(convId, { role: 'user', content: msg })
  addMessageTo(convId, { role: 'assistant', content: '', pending: true })
  sending.value = true
  scrollToBottom()

  try {
    const resp = await sendChat(currentConv.value.thread_id, msg)
    const data = resp.data
    const assistant = {
      role: 'assistant',
      content: data.reply || '（无回复）',
      sources: data.sources || [],
      pendingQuestion: data.pending_question || '',
      outcome: data.outcome || ''
    }
    replaceLastIn(convId, assistant)
    // 自动转人工（§5.7）：后端 route=human_handoff 或 outcome=handoff → 进入转人工流程
    if (data.route === 'human_handoff' || data.outcome === 'handoff') {
      beginHandoff(convId)
    }
  } catch {
    replaceLastIn(convId, { role: 'assistant', content: '请求失败，请稍后重试。', error: true })
    ElMessage.error('与服务的通信失败，请检查后端是否启动')
  } finally {
    sending.value = false
    scrollToBottom()
  }
}

function handleSend() {
  send()
}

// 手动转人工（§5.7 / §7）：复用反馈接口提交 bad_case + 前端三态推进
async function handleHandoff() {
  if (!currentConv.value) return
  if (isHandoffBusy.value || isHandoffHuman.value) {
    ElMessage({
      type: 'warning',
      message: isHandoffHuman.value ? '人工客服已接入，无需重复转接' : '已在转接中，请稍候'
    })
    return
  }
  const msgs = currentMessages.value
  let question = ''
  for (let i = msgs.length - 1; i >= 0; i--) {
    if (msgs[i].role === 'user') {
      question = msgs[i].content
      break
    }
  }
  if (!question) {
    ElMessage({ type: 'warning', message: '请先描述您的问题，再转接人工' })
    return
  }
  try {
    await submitBadCase({ thread_id: currentConv.value.thread_id, question, reply: '', note: '对话页手动转人工' })
    beginHandoff(currentId.value)
    ElMessage.success('已发起转人工，正在为您接入')
  } catch {
    ElMessage.error('转人工失败，请稍后重试')
  }
}

// 转人工流程：先转人工处理中(琥珀)，模拟约 3 秒后「浙小工」接入(蓝)。
// 真实客服后端未接入，故 human 态由前端 setTimeout 推进；状态仅本地持久化。
function beginHandoff(convId) {
  // 防重入：已在转接中或人工已接入则不重复触发
  if (isHandoffBusy.value || isHandoffHuman.value) return
  setHandoff(convId, HANDOFF.TRANSFERRING)
  addMessageTo(convId, {
    role: 'system',
    content: '正在为您转接人工客服 · 会话摘要已附带 · 预计等待 2 分钟'
  })
  scrollToBottom()
  setTimeout(() => {
    setHandoff(convId, HANDOFF.HUMAN)
    addMessageTo(convId, {
      role: 'system',
      content: '人工客服「浙小工」已接入会话，您可以继续描述问题'
    })
    scrollToBottom()
  }, 3000)
}

// 反馈（没解决 → bad_case，带 agent 回复）
async function handleFeedback(msg, idx) {
  const msgs = currentMessages.value
  let question = ''
  for (let i = idx - 1; i >= 0; i--) {
    if (msgs[i].role === 'user') {
      question = msgs[i].content
      break
    }
  }
  if (!question) return
  let note = ''
  try {
    const { value } = await ElMessageBox.prompt('如果愿意，可以补充说明（选填）：', '反馈未解决', {
      confirmButtonText: '提交反馈',
      cancelButtonText: '取消',
      inputPlaceholder: '选填',
      inputValidator: () => true
    })
    note = value || ''
  } catch {
    return
  }
  try {
    await submitBadCase({
      thread_id: currentConv.value.thread_id,
      question,
      reply: msg.content,
      note
    })
    msg.feedbackSubmitted = true
    ElMessage.success('已反馈，管理员审核后会补充到知识库')
  } catch {
    ElMessage.error('反馈提交失败，请稍后重试')
  }
}

async function handleSuggest() {
  let question = ''
  try {
    const { value } = await ElMessageBox.prompt('请描述您想问但没有答案的问题：', '提建议', {
      confirmButtonText: '下一步',
      cancelButtonText: '取消',
      inputValidator: (v) => (v && v.trim() ? true : '问题不能为空')
    })
    question = value.trim()
  } catch {
    return
  }
  let note = ''
  try {
    const { value } = await ElMessageBox.prompt('补充说明（选填）：', '提建议', {
      confirmButtonText: '提交',
      cancelButtonText: '跳过',
      inputPlaceholder: '选填'
    })
    note = value || ''
  } catch {
    /* 跳过 */
  }
  try {
    await submitSuggestion({ question, note })
    ElMessage.success('建议已提交，感谢反馈！')
  } catch {
    ElMessage.error('提交失败，请稍后重试')
  }
}

async function handleClear() {
  try {
    await ElMessageBox.confirm('确定删除该会话？历史记录不可恢复。', '删除会话', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消'
    })
  } catch {
    return
  }
  await deleteConversation(currentId.value)
  if (!conversations.value.length) {
    await newConversation()
  }
  ElMessage.success('会话已删除')
}

watch(
  () => currentMessages.value.length,
  () => scrollToBottom()
)
watch(draft, () => autoGrow())
</script>

<style scoped>
.chat-page {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
  background: var(--cd-bg);   /* 整页统一最浅蓝底，去掉硬色块拼接 */
}

/* 顶部（去边框，与消息区同底色，褪为"信息带"而非"硬分区"） */
.chat-head {
  padding: 18px 22px 14px;
  display: flex;
  align-items: center;
  gap: 10px;
  background: transparent;
  flex-shrink: 0;
}

.chat-head .ttl {
  font-size: 15px;
  font-weight: 700;
  color: var(--cd-text-1);
}

.chat-head .sub {
  font-size: 12px;
  color: var(--cd-text-3);
}

.status {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--cd-green);
  background: var(--cd-green-bg);
  border: 1px solid #d6f2e4;
  border-radius: 20px;
  padding: 4px 11px;
  font-weight: 600;
}

.status .dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--cd-green);
  animation: pulse 1.6s infinite;
}

@keyframes pulse {
  0% { box-shadow: 0 0 0 0 rgba(6, 118, 71, 0.35); }
  70% { box-shadow: 0 0 0 6px rgba(6, 118, 71, 0); }
  100% { box-shadow: 0 0 0 0 rgba(6, 118, 71, 0); }
}

.status.busy {
  color: var(--cd-amber);
  background: var(--cd-amber-bg);
  border-color: #fbe7c9;
}

.status.busy .dot {
  background: var(--cd-amber);
}

.status.human {
  color: var(--cd-primary);
  background: var(--cd-primary-soft);
  border-color: #d6e4f7;
}

.status.human .dot {
  background: var(--cd-primary);
  animation: none;
}

/* 消息区（与整页同底色，不再分色；让消息气泡本身做视觉锚点） */
.chat-body {
  flex: 1;
  overflow-y: auto;
  background: transparent;
  padding: 20px 10px 28px;
}

.chat-inner {
  max-width: 760px;
  margin: 0 auto;
  padding: 0 12px;
}

.welcome {
  text-align: center;
  padding: 48px 0 30px;
}

.welcome-icon {
  width: 52px;
  height: 52px;
  border-radius: 15px;
  background: var(--cd-grad);
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 0 auto 16px;
  box-shadow: 0 6px 18px rgba(20, 84, 156, 0.28);
}

.welcome-icon svg {
  width: 28px;
  height: 28px;
  stroke: #fff;
  stroke-width: 1.6;
  fill: none;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.welcome h2 {
  font-size: 20px;
  font-weight: 700;
  letter-spacing: 0.2px;
  color: var(--cd-text-1);
  margin: 0 0 8px;
}

.welcome p {
  font-size: 13px;
  color: var(--cd-text-3);
  line-height: 1.9;
}

.msg-row {
  display: flex;
  margin-bottom: 22px;
}

.msg-row.user {
  justify-content: flex-end;
}

.bubble {
  max-width: 620px;
  background: #fff;
  border: 1px solid var(--cd-line);
  border-radius: var(--cd-radius-bubble);
  border-top-left-radius: 6px;
  padding: 14px 17px;
  font-size: 14px;
  line-height: 1.75;
  box-shadow: var(--cd-shadow-sm);
  color: var(--cd-text-1);
  white-space: pre-wrap;
  word-break: break-word;
}

.msg-row.user .bubble {
  background: var(--cd-primary-soft);
  border-color: #d6e4f7;
  border-radius: var(--cd-radius-bubble);
  border-top-right-radius: 6px;
}

.bubble.error {
  color: var(--cd-danger);
  border-color: #f3c1c1;
  background: #fdf3f3;
}

/* 来源 chip */
.src {
  margin-top: 11px;
  font-size: 12px;
  color: var(--cd-text-3);
  border-top: 1px dashed var(--cd-line);
  padding-top: 9px;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  border-radius: 20px;
  padding: 2px 10px;
  font-size: 11.5px;
  font-weight: 600;
}

.chip.tool {
  background: var(--cd-primary-soft);
  color: var(--cd-primary);
}

.chip.kb {
  background: #eef2fb;
  color: #3b5bb0;
}

.chip-ic {
  width: 12px;
  height: 12px;
  stroke: currentColor;
  stroke-width: 1.8;
  fill: none;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.src-detail {
  font-family: 'SF Mono', 'Cascadia Code', Consolas, monospace;
  font-size: 11.5px;
}

/* 追问提示 */
.pending-tip {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 10px;
  font-size: 12.5px;
  color: var(--cd-amber);
  background: var(--cd-amber-bg);
  border: 1px solid #fbe7c9;
  border-radius: 8px;
  padding: 6px 11px;
}

.pending-tip .ic {
  width: 13px;
  height: 13px;
  stroke: currentColor;
  stroke-width: 1.8;
  fill: none;
}

/* 反馈 */
.fb {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: var(--cd-text-3);
  margin-left: 8px;
  cursor: pointer;
  user-select: none;
}

.fb:hover {
  color: var(--cd-primary);
}

.fb.done {
  color: var(--cd-green);
  cursor: default;
}

/* 思考中 */
.thinking {
  display: inline-flex;
  gap: 4px;
  padding: 4px 2px;
}

.thinking-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--cd-text-3);
  animation: blink 1s infinite;
}

.thinking-dot:nth-child(2) { animation-delay: 0.15s; }
.thinking-dot:nth-child(3) { animation-delay: 0.3s; }

@keyframes blink {
  0%, 80%, 100% { opacity: 0.25; transform: translateY(0); }
  40% { opacity: 1; transform: translateY(-3px); }
}

/* 系统提示条 */
.sys-note {
  display: flex;
  align-items: center;
  gap: 8px;
  justify-content: center;
  margin: 4px auto 20px;
  max-width: 560px;
  background: #fff;
  border: 1px dashed #d6e4f7;
  color: var(--cd-text-2);
  border-radius: 12px;
  padding: 10px 16px;
  font-size: 12.5px;
  box-shadow: var(--cd-shadow-sm);
}

.sys-note .ic {
  width: 15px;
  height: 15px;
  stroke: var(--cd-primary);
  stroke-width: 1.7;
  fill: none;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.sys-note b {
  color: var(--cd-primary);
}

/* 快捷问题 */
.quick {
  max-width: 760px;
  margin: 0 auto;
  padding: 16px 12px 2px;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  width: 100%;
}

.qlabel {
  width: auto;
  flex-shrink: 0;
  font-size: 11.5px;
  color: var(--cd-text-3);
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 6px;
}

.qlabel .ic {
  width: 13px;
  height: 13px;
  stroke: currentColor;
  stroke-width: 1.8;
  fill: none;
}

.qchip {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  border: 1px solid #d6e4f7;
  background: #fff;
  border-radius: 22px;
  padding: 8px 15px;
  font-size: 13px;
  color: var(--cd-primary);
  cursor: pointer;
  transition: all 0.14s;
  font-weight: 500;
}

.qchip:hover {
  background: var(--cd-primary-soft);
  border-color: var(--cd-primary);
  box-shadow: var(--cd-shadow-sm);
  transform: translateY(-1px);
}

/* 输入区（与整页同底色、无上边框，让唯一的白卡片输入框浮起来） */
.composer {
  background: transparent;
  padding: 4px 12px 18px;
  flex-shrink: 0;
}

.composer .actions {
  max-width: 760px;
  margin: 0 auto;
  display: flex;
  gap: 8px;
  padding: 6px 0 10px;
  flex-wrap: wrap;
}

.ghost-btn {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  border: 1px solid var(--cd-line);
  background: #fff;
  border-radius: 22px;
  padding: 6px 14px;
  font-size: 12.5px;
  color: var(--cd-text-2);
  cursor: pointer;
  transition: all 0.14s;
}

.ghost-btn:hover {
  border-color: var(--cd-primary);
  color: var(--cd-primary);
  box-shadow: var(--cd-shadow-sm);
}

.ghost-btn.warn:hover {
  border-color: #cbd5e1;
  color: var(--cd-text-1);
}

.ghost-btn .ic {
  width: 14px;
  height: 14px;
  stroke: currentColor;
  stroke-width: 1.7;
  fill: none;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.box {
  max-width: 760px;
  margin: 0 auto;
  display: flex;
  gap: 10px;
  align-items: flex-start;      /* textarea 贴顶，不再被高按钮沉到底 */
  background: #fff;
  border: 1px solid var(--cd-line-2);
  border-radius: 16px;
  padding: 8px 12px;            /* 减小上下内边距，文字更贴近框顶 */
  box-shadow: var(--cd-shadow-sm);
  transition: border-color 0.15s, box-shadow 0.15s;
}

.box:focus-within {
  border-color: var(--cd-primary);
  box-shadow: 0 0 0 3px rgba(20, 84, 156, 0.1), var(--cd-shadow-sm);
}

.box textarea {
  flex: 1;
  min-height: 24px;          /* 单行底线高度；防止空内容时 textarea 被 placeholder 撑高 */
  border: none;
  background: transparent;
  resize: none;
  outline: none;
  font-family: inherit;
  font-size: 14px;
  line-height: 1.5;
  max-height: 120px;
  color: var(--cd-text-1);
  padding: 2px 0;           /* 文字轻微内缩，避免贴死边 */
}

.send {
  width: 40px;
  height: 40px;
  align-self: center;        /* 发送按钮在框内垂直居中，而不是被 flex-start 推到顶 */
  border-radius: 12px;
  background: var(--cd-grad);
  border: none;
  color: #fff;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  box-shadow: 0 4px 12px rgba(20, 84, 156, 0.3);
  transition: filter 0.15s;
}

.send svg {
  width: 17px;
  height: 17px;
  stroke: #fff;
  stroke-width: 2;
  fill: none;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.send:hover {
  filter: brightness(1.05);
}

.send:disabled {
  opacity: 0.5;
  cursor: not-allowed;
  filter: none;
}

.tip {
  max-width: 760px;
  margin: 8px auto 0;
  text-align: center;
  font-size: 11.5px;
  color: var(--cd-text-3);
}
</style>
