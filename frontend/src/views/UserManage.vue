<template>
  <div class="page">
    <div class="cd-ph">
      <div>
        <h2>用户管理</h2>
        <p>增删改 · 角色 · 附加权限位 · 重置密码</p>
      </div>
      <button class="cd-btn" @click="openCreate">
        <svg viewBox="0 0 24 24" class="ic"><path d="M12 5v14M5 12h14"/></svg>
        新增用户
      </button>
    </div>
    <div class="content">
      <div class="cd-table-card">
        <table>
          <thead>
            <tr><th>账号</th><th>姓名</th><th>角色</th><th>附加权限位</th><th>状态</th><th style="width:150px">操作</th></tr>
          </thead>
          <tbody>
            <tr v-for="u in items" :key="u.id">
              <td class="mono">{{ u.id }}</td>
              <td>{{ u.name }}</td>
              <td><span class="cd-tag" :class="roleTag(u.role)">{{ roleLabel(u.role) }}</span></td>
              <td class="mono">{{ u.permissions.join(', ') || '—' }}</td>
              <td><span class="cd-tag" :class="u.enabled ? 'green' : 'gray'">{{ u.enabled ? '启用' : '已禁用' }}</span></td>
              <td>
                <span class="row-act" @click="openEdit(u)">编辑</span>
                <span class="row-act" @click="openReset(u)">重置密码</span>
              </td>
            </tr>
            <tr v-if="!items.length"><td colspan="6"><div class="cd-empty">暂无用户</div></td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- 新增 / 编辑弹窗 -->
    <div class="overlay" :class="{ open: formVisible }" @click.self="formVisible = false">
      <div class="modal-box">
        <div class="mh">{{ editing ? `编辑用户 ${editing.id}` : '新增用户' }}</div>
        <div class="mb">
          <div v-if="!editing">
            <label>账号（登录名）</label>
            <input v-model="form.id" placeholder="如 student-020" />
          </div>
          <div>
            <label>姓名</label>
            <input v-model="form.name" placeholder="姓名" />
          </div>
          <div>
            <label>角色</label>
            <select v-model="form.role">
              <option value="student">student（学生）</option>
              <option value="cs_staff">cs_staff（客服）</option>
              <option value="admin">admin（管理员）</option>
            </select>
          </div>
          <div v-if="!editing">
            <label>初始密码</label>
            <input v-model="form.password" type="password" placeholder="至少 6 位" />
          </div>
          <div v-if="editing">
            <label>启用状态</label>
            <div class="checkrow"><input v-model="form.enabled" type="checkbox" /> 允许登录</div>
          </div>
          <div>
            <label>附加权限位（最终权限 = 角色默认 ∪ 附加位；学生不可携带）</label>
            <div class="perm-grid">
              <label v-for="p in grantable" :key="p.key">
                <input v-model="form.permissions" type="checkbox" :value="p.key" />
                {{ p.label }}
              </label>
            </div>
          </div>
        </div>
        <div class="mf">
          <button class="cd-btn ghost" @click="formVisible = false">取消</button>
          <button class="cd-btn" :disabled="submitting" @click="handleSubmit">保存</button>
        </div>
      </div>
    </div>

    <!-- 重置密码弹窗 -->
    <div class="overlay" :class="{ open: resetVisible }" @click.self="resetVisible = false">
      <div class="modal-box">
        <div class="mh">重置密码 · {{ resetTarget }}</div>
        <div class="mb">
          <label>新密码</label>
          <input v-model="resetPwd" type="password" placeholder="至少 6 位" />
        </div>
        <div class="mf">
          <button class="cd-btn ghost" @click="resetVisible = false">取消</button>
          <button class="cd-btn" :disabled="submitting" @click="handleReset">确认重置</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { createUser, fetchUsers, resetPassword, updateUser } from '../api/admin'
import { GRANTABLE_PERMS } from '../constants/perms'

const ROLE_LABEL = { student: 'student', cs_staff: 'cs_staff', admin: 'admin' }

const items = ref([])
const formVisible = ref(false)
const editing = ref(null)
const submitting = ref(false)
const resetVisible = ref(false)
const resetTarget = ref('')
const resetPwd = ref('')
const grantable = GRANTABLE_PERMS

const form = reactive({ id: '', name: '', role: 'student', password: '', permissions: [], enabled: true })

async function load() {
  try {
    const resp = await fetchUsers()
    items.value = resp.data.items || []
  } catch {
    ElMessage.error('加载失败，请检查后端是否启动')
    items.value = []
  }
}

function roleLabel(r) {
  return ROLE_LABEL[r] || r
}

function roleTag(r) {
  return r === 'admin' ? 'amber' : r === 'cs_staff' ? 'indigo' : 'gray'
}

function openCreate() {
  editing.value = null
  Object.assign(form, { id: '', name: '', role: 'student', password: '', permissions: [], enabled: true })
  formVisible.value = true
}

function openEdit(u) {
  editing.value = u
  Object.assign(form, {
    id: u.id, name: u.name, role: u.role, password: '',
    permissions: [...u.permissions], enabled: u.enabled
  })
  formVisible.value = true
}

async function handleSubmit() {
  submitting.value = true
  try {
    if (editing.value) {
      await updateUser(editing.value.id, {
        role: form.role,
        permissions: form.permissions,
        enabled: form.enabled
      })
      ElMessage.success('已保存（用户需重新登录生效）')
    } else {
      if (!form.id || !form.name || !form.password) {
        ElMessage.warning('请完整填写账号、姓名、初始密码')
        return
      }
      await createUser({
        id: form.id.trim(),
        name: form.name.trim(),
        role: form.role,
        password: form.password,
        permissions: form.permissions
      })
      ElMessage.success('已创建用户')
    }
    formVisible.value = false
    await load()
  } catch (e) {
    const detail = e.response && e.response.data && e.response.data.detail
    ElMessage.error(detail || '保存失败')
  } finally {
    submitting.value = false
  }
}

function openReset(u) {
  resetTarget.value = u.id
  resetPwd.value = ''
  resetVisible.value = true
}

async function handleReset() {
  if (!resetPwd.value || resetPwd.value.length < 6) {
    ElMessage.warning('密码至少 6 位')
    return
  }
  submitting.value = true
  try {
    await resetPassword(resetTarget.value, { password: resetPwd.value })
    ElMessage.success('密码已重置')
    resetVisible.value = false
  } catch {
    ElMessage.error('重置失败')
  } finally {
    submitting.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.page {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
  background: var(--cd-bg);
}

.content {
  padding: 24px 28px;
  overflow-y: auto;
  flex: 1;
}

.mono {
  font-family: 'SF Mono', 'Cascadia Code', Consolas, monospace;
  color: var(--cd-text-2);
  font-size: 12.5px;
}

.row-act {
  color: var(--cd-primary);
  cursor: pointer;
  margin-right: 14px;
  font-size: 13px;
  font-weight: 500;
}

.row-act:hover {
  text-decoration: underline;
}

.ic {
  width: 14px;
  height: 14px;
  stroke: currentColor;
  stroke-width: 1.8;
  fill: none;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.cd-tag.indigo {
  background: #eef2fb;
  color: #3b5bb0;
}

.overlay {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.45);
  display: none;
  z-index: 50;
  align-items: center;
  justify-content: center;
}

.overlay.open {
  display: flex;
}

.modal-box {
  width: 460px;
  max-width: 94vw;
  background: #fff;
  border-radius: 16px;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.25);
  overflow: hidden;
}

.mh {
  padding: 20px 24px;
  border-bottom: 1px solid var(--cd-line);
  font-size: 16px;
  font-weight: 700;
  color: var(--cd-text-1);
}

.mb {
  padding: 22px 24px;
  display: grid;
  gap: 15px;
}

.mb label {
  font-size: 12.5px;
  color: var(--cd-text-2);
  display: block;
  margin-bottom: 7px;
  font-weight: 600;
}

.mb input,
.mb select {
  width: 100%;
  font-family: inherit;
  font-size: 13.5px;
  padding: 10px 12px;
  border: 1px solid var(--cd-line);
  border-radius: var(--cd-radius-btn);
  background: #fff;
  color: var(--cd-text-1);
}

.mb input:focus,
.mb select:focus {
  outline: none;
  border-color: var(--cd-primary);
  box-shadow: 0 0 0 3px rgba(20, 84, 156, 0.1);
}

.perm-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 9px;
}

.perm-grid label {
  display: flex;
  align-items: center;
  gap: 9px;
  font-weight: 500;
  font-size: 13px;
  background: var(--cd-panel);
  border: 1px solid var(--cd-line);
  border-radius: 10px;
  padding: 10px 12px;
  margin: 0;
  cursor: pointer;
  color: var(--cd-text-1);
}

.perm-grid input {
  width: auto;
}

.checkrow {
  display: flex;
  align-items: center;
  gap: 9px;
  font-size: 13px;
  color: var(--cd-text-2);
}

.checkrow input {
  width: auto;
}

.mf {
  padding: 16px 24px;
  border-top: 1px solid var(--cd-line);
  display: flex;
  gap: 10px;
  justify-content: flex-end;
}
</style>
