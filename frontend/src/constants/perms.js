// M4 权限体系（设计文档 §2）：角色默认权限 ∪ 附加权限位 = 最终权限
// M6-ZJUT 起运行时以 DB 为准：登录响应 user.permissions 已是角色权限(role_permissions 表) ∪
// 附加位的最终并集；下方 ROLE_PERMS / GRANTABLE_PERMS 仅作兜底（种子源），
// 用户管理页的勾选项已改为查 /api/admin/roles、/api/admin/permissions 接口。

export const PERM = {
  CHAT: 'chat',
  CS_WORKBENCH: 'cs_workbench',
  KB_REVIEW: 'kb_review',
  VIEW_STATS: 'view_stats',
  USER_MGMT: 'user_mgmt',
  VIEW_LOGS: 'view_logs'
}

// 角色默认权限位（与后端 require_perm 同源）
export const ROLE_PERMS = {
  student: [PERM.CHAT],
  cs_staff: [PERM.CHAT, PERM.CS_WORKBENCH],
  admin: [
    PERM.CHAT,
    PERM.CS_WORKBENCH,
    PERM.KB_REVIEW,
    PERM.VIEW_STATS,
    PERM.USER_MGMT,
    PERM.VIEW_LOGS
  ]
}

// 可被 admin 授予的附加权限位（用户管理页勾选用）
export const GRANTABLE_PERMS = [
  { key: PERM.CS_WORKBENCH, label: '客服工作台（接待标记）' },
  { key: PERM.KB_REVIEW, label: '知识库审查补入' },
  { key: PERM.VIEW_STATS, label: '数据看板' },
  { key: PERM.USER_MGMT, label: '用户管理' },
  { key: PERM.VIEW_LOGS, label: '日志管理' }
]

// 侧栏角色菜单（按最终权限过滤）
export const PERM_MENUS = [
  { path: '/cs', title: '客服工作台', icon: 'Headset', perm: PERM.CS_WORKBENCH },
  { path: '/admin', title: '知识库管理', icon: 'Collection', perm: PERM.KB_REVIEW },
  { path: '/stats', title: '数据看板', icon: 'DataLine', perm: PERM.VIEW_STATS },
  { path: '/users', title: '用户管理', icon: 'UserFilled', perm: PERM.USER_MGMT },
  { path: '/logs', title: '日志管理', icon: 'Document', perm: PERM.VIEW_LOGS }
]

// 最终权限 = 角色默认 ∪ 附加位
// extra 兼容两种输入：后端登录响应的 permissions 是数组；本地可传逗号字符串
export function effectivePerms(role, extra = '') {
  const base = ROLE_PERMS[role] || [PERM.CHAT]
  const extras = (Array.isArray(extra) ? extra : String(extra || '').split(','))
    .map((s) => String(s).trim())
    .filter(Boolean)
  return [...new Set([...base, ...extras])]
}

export function hasPerm(role, extra, perm) {
  return effectivePerms(role, extra).includes(perm)
}

// 当前登录用户（localStorage 非响应式，调用方需自行加响应式依赖）
export function currentUser() {
  try {
    return JSON.parse(localStorage.getItem('cd_user') || '{}')
  } catch {
    return {}
  }
}
