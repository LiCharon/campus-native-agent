import client from './client'

// 真实后端契约（src/campus_desk/api/routes/auth.py）
// POST /api/auth/login
//   请求体：{ username, password }   // username = users.id 或 student_no
//   成功返回（扁平、无 {code,msg,data} 包装）：
//     { token, expires_in, user: { id, name, role, dept, student_no, permissions } }
// 错误：401 {detail:"用户名或密码错误"} / 403 {detail:"账号已禁用，请联系管理员"}
// 注：后端暂未提供 register / captcha / refresh / reset / logout 接口

// POST /api/auth/login → 扁平 LoginResponse
export function login(account, password) {
  return client.post('/auth/login', { username: account, password }).then((res) => res.data)
}

// 登录态落盘：cd_token=token；cd_user={id,account,name,role,permissions}
// - account=id，兼容 useChat 读取 cd_user.id
// - permissions 兼容路由守卫 / 菜单的 effectivePerms
export function saveAuth(data) {
  const u = data.user || {}
  localStorage.setItem('cd_token', data.token)
  localStorage.setItem(
    'cd_user',
    JSON.stringify({
      id: u.id,
      account: u.id,
      name: u.name,
      role: u.role,
      permissions: u.permissions || []
    })
  )
}

export function clearAuth() {
  localStorage.removeItem('cd_token')
  localStorage.removeItem('cd_user')
  localStorage.removeItem('cd_refresh')
}

// 后端无 /auth/logout 端点：仅清本地登录态（安全，无服务端会话）
export function logout() {
  clearAuth()
}
