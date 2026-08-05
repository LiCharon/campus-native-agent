import client from './client'

// POST /api/auth/login  body {"username","password"}
// → {"token","expires_in","user":{"id","name","role","dept","student_no"}}
export function login(username, password) {
  return client.post('/auth/login', { username, password })
}

// 本地登出：清登录态即可（无服务端会话）
export function logout() {
  localStorage.removeItem('cd_token')
  localStorage.removeItem('cd_user')
}
