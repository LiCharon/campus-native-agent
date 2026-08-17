import axios from 'axios'

// axios 实例：所有接口以 /api 为前缀（vite dev 代理到 http://localhost:8000）
const client = axios.create({
  baseURL: '/api',
  timeout: 60000 // LLM 多轮处理可能耗时数秒，放宽超时
})

// 请求拦截器：注入 Bearer token
client.interceptors.request.use((config) => {
  const token = localStorage.getItem('cd_token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// 响应拦截器：401 清登录态回登录页；403 派发权限变更事件
// 注：后端无 refresh 端点（单 token），故不做自动续期
client.interceptors.response.use(
  (response) => response,
  (error) => {
    const status = error.response && error.response.status
    if (status === 401) {
      const onLogin = window.location.pathname.includes('/login')
      if (!onLogin) {
        // 会话过期：清登录态并跳登录页（登录页自身的 401 由 Login.vue 读取 detail 提示）
        localStorage.removeItem('cd_token')
        localStorage.removeItem('cd_user')
        localStorage.removeItem('cd_refresh')
        if (window.location.pathname !== '/login') {
          window.location.href = '/login'
        }
      }
      return Promise.reject(error)
    }
    if (status === 403) {
      // 对抗性审查 #5：改权限后旧 token 失效，提示重新登录而非静默失败
      const msg = error.response.data && error.response.data.detail
      window.dispatchEvent(
        new CustomEvent('cd-perm-denied', {
          detail: msg === '账号已禁用，请联系管理员' ? msg : '权限变更或无权访问，请重新登录'
        })
      )
    }
    return Promise.reject(error)
  }
)

export default client
