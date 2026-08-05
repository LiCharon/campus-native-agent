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

// 响应拦截器：401（token 过期/未登录）清登录态并回登录页
client.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response && error.response.status === 401) {
      localStorage.removeItem('cd_token')
      localStorage.removeItem('cd_user')
      if (window.location.pathname !== '/login') {
        window.location.href = '/login'
      }
    }
    return Promise.reject(error)
  }
)

export default client
