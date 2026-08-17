<template>
  <div class="login-wrap">
    <!-- 左品牌区（布局 B：仅 ≥1024 显示） -->
    <aside class="left">
      <div class="brand">
        <div class="logo"><i class="ic-campus"></i></div>
        <div class="wm">
          <b>{{ BRAND.name }}</b>
          <small>{{ BRAND.subtitle }}</small>
        </div>
      </div>

      <div class="slogan">校园办事，<br />一站搞定。</div>

      <ul class="features">
        <li>
          <span class="fi"><i class="ic-search"></i></span>
          <div><b>空教室 / 座位实时查</b><small>上课前先看哪儿有空位</small></div>
        </li>
        <li>
          <span class="fi"><i class="ic-book"></i></span>
          <div><b>办事流程 / 知识库</b><small>补卡、证明、申请一步步指引</small></div>
        </li>
        <li>
          <span class="fi"><i class="ic-headset"></i></span>
          <div><b>未解决一键转人工</b><small>复杂问题直达客服工作台</small></div>
        </li>
      </ul>

      <div class="left-foot">{{ BRAND.loginFoot }}</div>

      <!-- 装饰：校舍 + 光斑 -->
      <svg class="deco" viewBox="0 0 320 200" aria-hidden="true">
        <g fill="none" stroke="rgba(255,255,255,.5)" stroke-width="2">
          <path d="M40 160 L160 70 L280 160" />
          <path d="M70 160 L160 100 L250 160" />
          <line x1="160" y1="70" x2="160" y2="40" />
          <rect x="120" y="160" width="80" height="40" />
        </g>
      </svg>
      <span class="spot s1"></span>
      <span class="spot s2"></span>
    </aside>

    <!-- 右表单区 -->
    <main class="right">
      <div class="login-card">
        <div class="card-head">
          <h1>{{ mode === 'login' ? '欢迎回来' : '注册账号' }}</h1>
          <p>{{ mode === 'login' ? '登录你的校园账号' : '创建你的校园账号' }}</p>
        </div>

        <div class="login-err" v-if="inlineError">
          <i class="ic-warn"></i><span>{{ inlineError }}</span>
        </div>

        <div class="pending-banner" v-if="mode === 'register'">
          <i class="ic-warn"></i>
          注册功能待后端接入，账号由管理员在「用户管理」创建
        </div>

        <el-form
          ref="formRef"
          :model="form"
          :rules="rules"
          @submit.prevent="onSubmit"
          label-position="top"
        >
          <el-form-item prop="account">
            <el-input
              v-model="form.account"
              placeholder="请输入账号（如 student-001）"
              @input="inlineError = ''"
            >
              <template #prefix><i class="ic-user"></i></template>
            </el-input>
          </el-form-item>

          <el-form-item prop="password" v-if="mode === 'login'">
            <el-input
              :type="showPwd ? 'text' : 'password'"
              v-model="form.password"
              placeholder="请输入密码"
              @input="inlineError = ''"
            >
              <template #prefix><i class="ic-lock"></i></template>
              <template #suffix>
                <span class="pwd-eye" @click="showPwd = !showPwd">
                  <i :class="showPwd ? 'ic-eye-off' : 'ic-eye'"></i>
                </span>
              </template>
            </el-input>
          </el-form-item>

          <!-- 注册：姓名 / 密码 / 确认密码（待后端接入，表单置灰） -->
          <template v-if="mode === 'register'">
            <el-form-item prop="name">
              <el-input v-model="form.name" disabled placeholder="请输入姓名 / 昵称">
                <template #prefix><i class="ic-user"></i></template>
              </el-input>
            </el-form-item>
            <el-form-item prop="password">
              <el-input
                :type="showPwd ? 'text' : 'password'"
                v-model="form.password"
                disabled
                placeholder="请设置密码（至少 6 位）"
              >
                <template #prefix><i class="ic-lock"></i></template>
              </el-input>
            </el-form-item>
            <el-form-item prop="confirm">
              <el-input
                :type="showPwd ? 'text' : 'password'"
                v-model="form.confirm"
                disabled
                placeholder="请再次输入密码"
              >
                <template #prefix><i class="ic-lock"></i></template>
              </el-input>
            </el-form-item>
          </template>

          <!-- 图形验证码：待后端接入（后端暂未提供 captcha 接口） -->
          <el-form-item label-width="0">
            <div class="captcha-row">
              <el-input disabled placeholder="图形验证码（待后端接入）">
                <template #prefix><i class="ic-shield"></i></template>
              </el-input>
              <span class="pending-tag">待后端</span>
            </div>
          </el-form-item>

          <div class="row-line" v-if="mode === 'login'">
            <el-checkbox v-model="form.remember" disabled>
              记住我<span class="pending-tag sm">待后端</span>
            </el-checkbox>
            <a class="link" @click="openForgot">忘记密码？</a>
          </div>

          <el-button
            class="submit-btn"
            native-type="submit"
            :loading="loading"
            :disabled="mode === 'register'"
          >{{ mode === 'login' ? '登 录' : '注 册（待后端）' }}</el-button>
        </el-form>

        <div class="switch">
          <template v-if="mode === 'login'">
            没有账号？<a class="link" @click="switchMode('register')">立即注册</a>
          </template>
          <template v-else>
            已有账号？<a class="link" @click="switchMode('login')">返回登录</a>
          </template>
        </div>
      </div>
      <div class="right-foot">{{ BRAND.demoFoot }}</div>
    </main>

    <!-- 忘记密码弹窗（待后端接入：仅提示联系管理员） -->
    <el-dialog
      v-model="forgotVisible"
      title="忘记密码"
      width="420px"
      align-center
    >
      <p class="dialog-tip">
        密码自助重置功能待后端接入。如需重置密码，请联系管理员在「用户管理 → 重置密码」处理。
      </p>
      <template #footer>
        <el-button class="submit-btn sm" @click="submitForgot">我知道了</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { login, saveAuth } from '../api/auth'
import { ROLE_HOME } from '../router'
import { BRAND } from '../config/brand'

const router = useRouter()

const mode = ref('login') // 'login' | 'register'
const loading = ref(false)
const showPwd = ref(false)
const inlineError = ref('')

const formRef = ref(null)
const form = reactive({
  account: '',
  name: '',
  password: '',
  confirm: '',
  captchaCode: '',
  remember: false
})

// 校验规则（随模式变化）
function validateConfirm(rule, value, cb) {
  if (value !== form.password) cb(new Error('两次输入的密码不一致'))
  else cb()
}
const rules = computed(() => {
  const account = [
    { required: true, message: '请输入账号', trigger: 'blur' },
    { pattern: /^[a-zA-Z0-9_-]{3,20}$/, message: '账号为 3-20 位字母、数字或 _-', trigger: 'blur' }
  ]
  if (mode.value === 'register') {
    // 注册功能待后端接入，规则仅占位
    return {
      account,
      name: [{ required: true, message: '请输入姓名 / 昵称', trigger: 'blur' }],
      password: [
        { required: true, message: '请设置密码', trigger: 'blur' },
        { min: 6, message: '密码至少 6 位', trigger: 'blur' }
      ],
      confirm: [
        { required: true, message: '请再次输入密码', trigger: 'blur' },
        { validator: validateConfirm, trigger: 'blur' }
      ]
    }
  }
  return {
    account,
    password: [{ required: true, message: '请输入密码', trigger: 'blur' }]
  }
})

function switchMode(m) {
  mode.value = m
  inlineError.value = ''
  formRef.value && formRef.value.clearValidate()
}

async function onSubmit() {
  if (!formRef.value) return
  if (mode.value === 'register') {
    // 注册功能待后端接入
    ElMessage.info('注册功能待后端接入')
    return
  }
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return
  await doLogin()
}

async function doLogin() {
  loading.value = true
  inlineError.value = ''
  try {
    // 真实后端：POST /api/auth/login → 扁平 { token, expires_in, user }
    const data = await login(form.account, form.password)
    saveAuth(data)
    const home = ROLE_HOME[data.user && data.user.role] || '/chat'
    ElMessage.success('登录成功')
    router.replace(home)
  } catch (e) {
    // 后端 401/403 通过 HTTPException 返回 {detail}，优先取之
    const detail = e && e.response && e.response.data && e.response.data.detail
    inlineError.value = detail || (e && e.message) || '登录失败，请重试'
  } finally {
    loading.value = false
  }
}

// 忘记密码：后端无自助重置，仅提示联系管理员（待后端接入）
const forgotVisible = ref(false)
function openForgot() {
  forgotVisible.value = true
}
function submitForgot() {
  forgotVisible.value = false
  ElMessage.info('密码重置功能待后端接入，请联系管理员')
}
</script>

<style scoped>
.login-wrap {
  display: flex;
  height: 100vh;
  background: var(--cd-bg);
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC',
    'Hiragino Sans GB', 'Microsoft YaHei', sans-serif;
}

/* ===== 左品牌区 ===== */
.left {
  position: relative;
  width: 38%;
  flex: 0 0 38%;
  padding: 56px 48px 40px;
  display: flex;
  flex-direction: column;
  color: #fff;
  background: var(--cd-grad);
  overflow: hidden;
}
.brand {
  display: flex;
  align-items: center;
  gap: 14px;
}
.logo {
  width: 46px;
  height: 46px;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.16);
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px solid rgba(255, 255, 255, 0.35);
}
.wm {
  display: flex;
  flex-direction: column;
  line-height: 1.3;
}
.wm b {
  font-size: 21px;
  font-weight: 700;
  letter-spacing: 0.3px;
}
.wm small {
  font-size: 12.5px;
  opacity: 0.85;
  margin-top: 2px;
}
.slogan {
  margin-top: auto;
  font-size: 34px;
  font-weight: 800;
  line-height: 1.25;
  letter-spacing: 1px;
}
.features {
  list-style: none;
  margin: 28px 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.features li {
  display: flex;
  align-items: center;
  gap: 14px;
}
.fi {
  width: 40px;
  height: 40px;
  border-radius: 11px;
  background: rgba(255, 255, 255, 0.16);
  display: flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
}
.features b {
  font-size: 14.5px;
  font-weight: 600;
}
.features small {
  display: block;
  font-size: 12px;
  opacity: 0.82;
  margin-top: 1px;
}
.left-foot {
  margin-top: 32px;
  font-size: 12.5px;
  opacity: 0.8;
  letter-spacing: 0.5px;
}
.deco {
  position: absolute;
  right: -40px;
  bottom: 40px;
  width: 320px;
  height: 200px;
  opacity: 0.5;
}
.spot {
  position: absolute;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.18);
  filter: blur(2px);
}
.s1 {
  width: 180px;
  height: 180px;
  top: -50px;
  right: -40px;
}
.s2 {
  width: 120px;
  height: 120px;
  bottom: 120px;
  left: -30px;
}

/* ===== 右表单区 ===== */
.right {
  position: relative;
  flex: 1 1 auto;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 40px;
  overflow: hidden;
  background:
    radial-gradient(900px 520px at 88% -10%, rgba(31, 111, 196, 0.20), transparent 60%),
    radial-gradient(780px 540px at -10% 116%, rgba(20, 84, 156, 0.18), transparent 55%),
    linear-gradient(135deg, #e8f0fb 0%, #f1f5fb 50%, #ffffff 100%);
}
/* 右侧背景装饰：两枚较实的角部光斑 */
.right::before {
  content: '';
  position: absolute;
  width: 440px;
  height: 440px;
  top: -150px;
  right: -120px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(20, 84, 156, 0.16), transparent 70%);
}
.right::after {
  content: '';
  position: absolute;
  width: 380px;
  height: 380px;
  bottom: -140px;
  left: -100px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(31, 111, 196, 0.14), transparent 70%);
}
.login-card {
  position: relative;
  z-index: 1;
  width: 100%;
  max-width: 400px;
  background: var(--cd-card);
  border: 1px solid var(--cd-line);
  border-radius: var(--cd-radius-card);
  box-shadow: var(--cd-shadow);
  padding: 36px 36px 30px;
}
.card-head h1 {
  margin: 0;
  font-size: 24px;
  font-weight: 700;
  color: var(--cd-text-1);
  letter-spacing: 0.3px;
}
.card-head p {
  margin: 6px 0 22px;
  font-size: 13px;
  color: var(--cd-text-3);
}
.login-err {
  display: flex;
  align-items: center;
  gap: 8px;
  background: var(--cd-amber-bg);
  color: var(--cd-amber);
  border: 1px solid #fdecc8;
  border-radius: 10px;
  padding: 9px 12px;
  font-size: 13px;
  margin-bottom: 16px;
}
:deep(.el-form-item) {
  margin-bottom: 18px;
}
:deep(.el-input__wrapper) {
  border-radius: var(--cd-radius-btn);
  padding-left: 10px;
}
.row-line {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: -4px 0 18px;
}
.link {
  color: var(--cd-primary);
  cursor: pointer;
  font-size: 13px;
  text-decoration: none;
}
.link:hover {
  text-decoration: underline;
}
.captcha-row {
  display: flex;
  gap: 10px;
  width: 100%;
}
.captcha-row .el-input {
  flex: 1 1 auto;
}
.captcha-img {
  width: 116px;
  height: 38px;
  border-radius: 8px;
  border: 1px solid var(--cd-line);
  cursor: pointer;
  flex: 0 0 auto;
  object-fit: cover;
  background: var(--cd-panel);
}
.submit-btn {
  width: 100%;
  border: none;
  border-radius: var(--cd-radius-btn);
  background: var(--cd-grad);
  color: #fff;
  font-size: 15px;
  font-weight: 600;
  letter-spacing: 2px;
  height: 44px;
  box-shadow: 0 6px 16px rgba(20, 84, 156, 0.22);
}
.submit-btn:hover {
  filter: brightness(1.05);
}
.submit-btn.sm {
  width: auto;
  height: 34px;
  letter-spacing: 0;
  padding: 0 16px;
  font-size: 13px;
}
.switch {
  margin-top: 18px;
  text-align: center;
  font-size: 13px;
  color: var(--cd-text-3);
}
.right-foot {
  position: relative;
  z-index: 1;
  margin-top: 22px;
  font-size: 12px;
  color: var(--cd-text-3);
  letter-spacing: 0.5px;
}
.dialog-tip {
  font-size: 13px;
  color: var(--cd-text-2);
  line-height: 1.7;
  margin: 0 0 16px;
}

/* 待后端接入：提示条 + 标签 */
.pending-banner {
  display: flex;
  align-items: center;
  gap: 8px;
  background: var(--cd-panel);
  color: var(--cd-text-2);
  border: 1px dashed var(--cd-line);
  border-radius: 10px;
  padding: 9px 12px;
  font-size: 12.5px;
  margin-bottom: 16px;
}
.pending-tag {
  display: inline-flex;
  align-items: center;
  flex: 0 0 auto;
  height: 22px;
  padding: 0 8px;
  border-radius: 6px;
  background: var(--cd-amber-bg);
  color: var(--cd-amber);
  font-size: 11.5px;
  font-weight: 600;
}
.pending-tag.sm {
  height: 18px;
  padding: 0 6px;
  margin-left: 4px;
  font-size: 10.5px;
}

/* ===== 线性 SVG 图标（currentColor） ===== */
[class^='ic-'] {
  display: inline-block;
  width: 17px;
  height: 17px;
  background-color: currentColor;
  -webkit-mask-repeat: no-repeat;
  mask-repeat: no-repeat;
  -webkit-mask-position: center;
  mask-position: center;
  -webkit-mask-size: contain;
  mask-size: contain;
  vertical-align: middle;
}
.ic-user {
  -webkit-mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='black' d='M12 12a5 5 0 1 0-5-5 5 5 0 0 0 5 5Zm0 2c-4 0-8 2-8 5v1h16v-1c0-3-4-5-8-5Z'/%3E%3C/svg%3E");
  mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='black' d='M12 12a5 5 0 1 0-5-5 5 5 0 0 0 5 5Zm0 2c-4 0-8 2-8 5v1h16v-1c0-3-4-5-8-5Z'/%3E%3C/svg%3E");
}
.ic-lock {
  -webkit-mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='black' d='M17 9V7a5 5 0 0 0-10 0v2H5v12h14V9Zm-2 0H9V7a3 3 0 0 1 6 0Z'/%3E%3C/svg%3E");
  mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='black' d='M17 9V7a5 5 0 0 0-10 0v2H5v12h14V9Zm-2 0H9V7a3 3 0 0 1 6 0Z'/%3E%3C/svg%3E");
}
.ic-eye {
  -webkit-mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='black' d='M12 5c-5 0-9 4.5-10 7 1 2.5 5 7 10 7s9-4.5 10-7c-1-2.5-5-7-10-7Zm0 11a4 4 0 1 1 0-8 4 4 0 0 1 0 8Z'/%3E%3C/svg%3E");
  mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='black' d='M12 5c-5 0-9 4.5-10 7 1 2.5 5 7 10 7s9-4.5 10-7c-1-2.5-5-7-10-7Zm0 11a4 4 0 1 1 0-8 4 4 0 0 1 0 8Z'/%3E%3C/svg%3E");
}
.ic-eye-off {
  -webkit-mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='black' d='M2 4.3 3.3 3 21 20.7 19.7 22l-3.2-3.2A11.6 11.6 0 0 1 12 19C7 19 3 14.5 2 12a13 13 0 0 1 4-4.6L2 4.3Zm9 4.7a4 4 0 0 1 4 4l-4-4Zm0 11a9.6 9.6 0 0 0 9-7 13 13 0 0 0-2.3-3.4L18 9.2A6 6 0 0 1 13 14.8L11 12.8A4 4 0 0 0 11 14c0 .5.1 1 .3 1.4L9.5 13.6A4 4 0 0 1 11 9Z'/%3E%3C/svg%3E");
  mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='black' d='M2 4.3 3.3 3 21 20.7 19.7 22l-3.2-3.2A11.6 11.6 0 0 1 12 19C7 19 3 14.5 2 12a13 13 0 0 1 4-4.6L2 4.3Zm9 4.7a4 4 0 0 1 4 4l-4-4Zm0 11a9.6 9.6 0 0 0 9-7 13 13 0 0 0-2.3-3.4L18 9.2A6 6 0 0 1 13 14.8L11 12.8A4 4 0 0 0 11 14c0 .5.1 1 .3 1.4L9.5 13.6A4 4 0 0 1 11 9Z'/%3E%3C/svg%3E");
}
.ic-shield {
  -webkit-mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='black' d='M12 2 4 5v6c0 5 3.4 9 8 11 4.6-2 8-6 8-11V5Z'/%3E%3C/svg%3E");
  mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='black' d='M12 2 4 5v6c0 5 3.4 9 8 11 4.6-2 8-6 8-11V5Z'/%3E%3C/svg%3E");
}
.ic-warn {
  -webkit-mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='black' d='M12 2 1 21h22ZM12 8a1.3 1.3 0 0 1 1.3 1.3v5a1.3 1.3 0 0 1-2.6 0v-5A1.3 1.3 0 0 1 12 8Zm0 9.5a1.2 1.2 0 1 1 0 2.4 1.2 1.2 0 0 1 0-2.4Z'/%3E%3C/svg%3E");
  mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='black' d='M12 2 1 21h22ZM12 8a1.3 1.3 0 0 1 1.3 1.3v5a1.3 1.3 0 0 1-2.6 0v-5A1.3 1.3 0 0 1 12 8Zm0 9.5a1.2 1.2 0 1 1 0 2.4 1.2 1.2 0 0 1 0-2.4Z'/%3E%3C/svg%3E");
}
.ic-search {
  -webkit-mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='black' d='M10 2a8 8 0 1 0 4.9 14.3l5.4 5.4 1.4-1.4-5.4-5.4A8 8 0 0 0 10 2Zm0 2a6 6 0 1 1 0 12 6 6 0 0 1 0-12Z'/%3E%3C/svg%3E");
  mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='black' d='M10 2a8 8 0 1 0 4.9 14.3l5.4 5.4 1.4-1.4-5.4-5.4A8 8 0 0 0 10 2Zm0 2a6 6 0 1 1 0 12 6 6 0 0 1 0-12Z'/%3E%3C/svg%3E");
}
.ic-book {
  -webkit-mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='black' d='M4 4h6a3 3 0 0 1 3 3v13a4 4 0 0 0-3-1.5H4Zm16 0h-6a3 3 0 0 0-3 3v13a4 4 0 0 1 3-1.5h6Z'/%3E%3C/svg%3E");
  mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='black' d='M4 4h6a3 3 0 0 1 3 3v13a4 4 0 0 0-3-1.5H4Zm16 0h-6a3 3 0 0 0-3 3v13a4 4 0 0 1 3-1.5h6Z'/%3E%3C/svg%3E");
}
.ic-headset {
  -webkit-mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='black' d='M12 3a9 9 0 0 0-9 9v5a3 3 0 0 0 3 3h1v-7H5v-1a7 7 0 0 1 14 0v1h-2v7h1a3 3 0 0 0 3-3v-5a9 9 0 0 0-9-9Z'/%3E%3C/svg%3E");
  mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='black' d='M12 3a9 9 0 0 0-9 9v5a3 3 0 0 0 3 3h1v-7H5v-1a7 7 0 0 1 14 0v1h-2v7h1a3 3 0 0 0 3-3v-5a9 9 0 0 0-9-9Z'/%3E%3C/svg%3E");
}
.ic-campus {
  width: 24px;
  height: 24px;
  -webkit-mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='black' d='M12 2 2 8v2h20V8Zm-7 9v6H4v2h16v-2h-1v-6h-2v6h-3v-6h-2v6H9v-6Z'/%3E%3C/svg%3E");
  mask-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='black' d='M12 2 2 8v2h20V8Zm-7 9v6H4v2h16v-2h-1v-6h-2v6h-3v-6h-2v6H9v-6Z'/%3E%3C/svg%3E");
}
.pwd-eye {
  cursor: pointer;
  color: var(--cd-text-3);
  display: inline-flex;
}
.pwd-eye:hover {
  color: var(--cd-primary);
}

/* ===== 响应式：<1024 隐藏左品牌区 ===== */
@media (max-width: 1023px) {
  .left {
    display: none;
  }
  .right {
    width: 100%;
  }
}
</style>
