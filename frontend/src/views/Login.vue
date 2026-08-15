<template>
  <div class="login-page">
    <div class="login-card">
      <div class="login-logo">
        <el-icon :size="34" color="#409eff"><Service /></el-icon>
      </div>
      <h1 class="login-title">Campus Native Agent</h1>
      <p class="login-subtitle">校园智能服务助手 · 登录</p>

      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-position="top"
        size="large"
        @keyup.enter="handleLogin"
      >
        <el-form-item label="账号" prop="username">
          <el-input
            v-model="form.username"
            placeholder="请输入账号"
            :prefix-icon="User"
            clearable
          />
        </el-form-item>
        <el-form-item label="密码" prop="password">
          <el-input
            v-model="form.password"
            type="password"
            placeholder="请输入密码"
            :prefix-icon="Lock"
            show-password
          />
        </el-form-item>
        <el-form-item>
          <el-button
            type="primary"
            class="login-btn"
            :loading="loading"
            @click="handleLogin"
          >
            登 录
          </el-button>
        </el-form-item>
      </el-form>

      <p class="login-hint">演示账号：student-001 / cs-001 / admin-001（密码 123456）</p>
    </div>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Service, User, Lock } from '@element-plus/icons-vue'
import { login } from '../api/auth'

const router = useRouter()

const formRef = ref(null)
const loading = ref(false)

const form = reactive({
  username: '',
  password: ''
})

const rules = {
  username: [{ required: true, message: '请输入账号', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }]
}

async function handleLogin() {
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return

  loading.value = true
  try {
    const resp = await login(form.username, form.password)
    const { token, user } = resp.data
    localStorage.setItem('cd_token', token)
    localStorage.setItem('cd_user', JSON.stringify(user))
    ElMessage.success(`欢迎回来，${user.name}`)
    router.push('/chat')
  } catch (err) {
    // 后端契约：401 = 用户名或密码错误
    ElMessage.error('用户名或密码错误')
    void err
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-page {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #e8f1ff 0%, #f5f7fa 55%, #eef7f4 100%);
}

.login-card {
  width: 400px;
  padding: 40px 36px 28px;
  background: #fff;
  border-radius: 12px;
  box-shadow: 0 8px 32px rgba(31, 45, 61, 0.12);
}

.login-logo {
  text-align: center;
  margin-bottom: 8px;
}

.login-title {
  margin: 0 0 6px;
  text-align: center;
  font-size: 22px;
  font-weight: 600;
  color: #303133;
}

.login-subtitle {
  margin: 0 0 26px;
  text-align: center;
  font-size: 13px;
  color: #909399;
}

.login-btn {
  width: 100%;
}

.login-hint {
  margin: 8px 0 0;
  text-align: center;
  font-size: 12px;
  color: #c0c4cc;
}
</style>
