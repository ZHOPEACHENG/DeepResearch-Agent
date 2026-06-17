<script setup lang="ts">
import { ref, reactive, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { createPasswordValidator, createConfirmPasswordValidator } from '@/utils/validators'
import type { FormInstance, FormRules } from 'element-plus'

const router = useRouter()
const authStore = useAuthStore()

// ── Profile Form ──────────────────────────────────────────────────────

const profileFormRef = ref<FormInstance>()
const profileForm = reactive({
  displayName: '',
  email: '',
  institution: '',
})

const profileRules: FormRules = {
  email: [
    { required: true, message: '请输入邮箱地址', trigger: 'blur' },
    { type: 'email', message: '邮箱格式不正确', trigger: 'blur' },
  ],
}

const profileSaving = ref(false)
const profileError = ref('')
const profileSuccess = ref('')
const _profilePopulated = ref(false)

function populateForm() {
  if (authStore.user && !_profilePopulated.value) {
    _profilePopulated.value = true
    profileForm.displayName = authStore.user.displayName || ''
    profileForm.email = authStore.user.email
    profileForm.institution = authStore.user.institution || ''
  }
}

// Populate form on mount and reactively when user data becomes available.
// Guard prevents overwriting in-progress edits on profile update response.
onMounted(populateForm)
watch(() => authStore.user, () => {
  if (authStore.user && !profileSaving.value) populateForm()
})

async function handleUpdateProfile() {
  const valid = profileFormRef.value ? await profileFormRef.value.validate().catch(() => false) : false
  if (!valid) return

  profileSaving.value = true
  profileError.value = ''
  profileSuccess.value = ''
  try {
    await authStore.updateProfile({
      displayName: profileForm.displayName || null,
      email: profileForm.email,
      institution: profileForm.institution || null,
    })
    profileSuccess.value = '个人资料更新成功'
  } catch {
    profileError.value = authStore.error || '更新失败，请重试'
  } finally {
    profileSaving.value = false
  }
}

// ── Password Form ─────────────────────────────────────────────────────

const passwordFormRef = ref<FormInstance>()
const passwordForm = reactive({
  oldPassword: '',
  newPassword: '',
  confirmNewPassword: '',
})

const passwordRules: FormRules = {
  oldPassword: [
    { required: true, message: '请输入当前密码', trigger: 'blur' },
  ],
  newPassword: [
    { required: true, message: '请输入新密码', trigger: 'blur' },
    { min: 8, message: '新密码至少 8 个字符', trigger: 'blur' },
    { validator: createPasswordValidator(), trigger: 'blur' },
  ],
  confirmNewPassword: [
    { required: true, message: '请确认新密码', trigger: 'blur' },
    {
      validator: createConfirmPasswordValidator(() => passwordForm.newPassword, '新密码'),
      trigger: 'blur',
    },
  ],
}

const passwordSaving = ref(false)
const passwordError = ref('')
const passwordSuccess = ref('')

async function handleChangePassword() {
  const valid = passwordFormRef.value ? await passwordFormRef.value.validate().catch(() => false) : false
  if (!valid) return

  passwordSaving.value = true
  passwordError.value = ''
  passwordSuccess.value = ''
  try {
    await authStore.changePassword(passwordForm.oldPassword, passwordForm.newPassword)
    passwordSuccess.value = '密码修改成功'
    passwordForm.oldPassword = ''
    passwordForm.newPassword = ''
    passwordForm.confirmNewPassword = ''
    passwordFormRef.value?.resetFields()
  } catch {
    passwordError.value = authStore.error || '密码修改失败'
  } finally {
    passwordSaving.value = false
  }
}

// ── Memoized formatting ──────────────────────────────────────────────
function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString('zh-CN')
}

// ── Navigation ────────────────────────────────────────────────────────

function goBack() {
  router.push('/chat')
}
</script>

<template>
  <div class="profile-page">
    <div class="profile-container">
      <div class="profile-header">
        <el-button text @click="goBack">← 返回对话</el-button>
        <h1>个人资料</h1>
      </div>

      <!-- Profile Section -->
      <el-card class="profile-section">
        <template #header>
          <span>账号信息</span>
        </template>

        <el-alert
          v-if="profileError"
          :title="profileError"
          type="error"
          show-icon
          closable
          class="section-alert"
          @close="profileError = ''"
        />
        <el-alert
          v-if="profileSuccess"
          :title="profileSuccess"
          type="success"
          show-icon
          closable
          class="section-alert"
          @close="profileSuccess = ''"
        />

        <el-form
          ref="profileFormRef"
          :model="profileForm"
          :rules="profileRules"
          label-position="top"
          @submit.prevent="handleUpdateProfile"
        >
          <el-form-item label="用户名">
            <el-input
              :model-value="authStore.user?.username"
              disabled
              size="large"
            />
          </el-form-item>

          <el-form-item label="邮箱" prop="email">
            <el-input
              v-model="profileForm.email"
              type="email"
              size="large"
              autocomplete="email"
            />
          </el-form-item>

          <el-row :gutter="16">
            <el-col :span="12">
              <el-form-item label="显示名称">
                <el-input
                  v-model="profileForm.displayName"
                  placeholder="可选"
                  size="large"
                  maxlength="100"
                />
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="所属机构">
                <el-input
                  v-model="profileForm.institution"
                  placeholder="可选"
                  size="large"
                  maxlength="200"
                />
              </el-form-item>
            </el-col>
          </el-row>

          <el-form-item>
            <el-button
              type="primary"
              :loading="profileSaving"
              :disabled="profileSaving"
              @click="handleUpdateProfile"
            >
              {{ profileSaving ? '保存中…' : '保存修改' }}
            </el-button>
          </el-form-item>
        </el-form>
      </el-card>

      <!-- Password Section -->
      <el-card class="profile-section">
        <template #header>
          <span>修改密码</span>
        </template>

        <el-alert
          v-if="passwordError"
          :title="passwordError"
          type="error"
          show-icon
          closable
          class="section-alert"
          @close="passwordError = ''"
        />
        <el-alert
          v-if="passwordSuccess"
          :title="passwordSuccess"
          type="success"
          show-icon
          closable
          class="section-alert"
          @close="passwordSuccess = ''"
        />

        <el-form
          ref="passwordFormRef"
          :model="passwordForm"
          :rules="passwordRules"
          label-position="top"
          @submit.prevent="handleChangePassword"
        >
          <el-form-item label="当前密码" prop="oldPassword">
            <el-input
              v-model="passwordForm.oldPassword"
              type="password"
              placeholder="输入当前密码"
              size="large"
              show-password
              autocomplete="current-password"
            />
          </el-form-item>

          <el-form-item label="新密码" prop="newPassword">
            <el-input
              v-model="passwordForm.newPassword"
              type="password"
              placeholder="至少 8 个字符，含大小写字母和数字"
              size="large"
              show-password
              autocomplete="new-password"
            />
          </el-form-item>

          <el-form-item label="确认新密码" prop="confirmNewPassword">
            <el-input
              v-model="passwordForm.confirmNewPassword"
              type="password"
              placeholder="再次输入新密码"
              size="large"
              show-password
              autocomplete="new-password"
            />
          </el-form-item>

          <el-form-item>
            <el-button
              type="warning"
              :loading="passwordSaving"
              :disabled="passwordSaving"
              @click="handleChangePassword"
            >
              {{ passwordSaving ? '修改中…' : '修改密码' }}
            </el-button>
          </el-form-item>
        </el-form>
      </el-card>

      <!-- Account Details -->
      <el-card class="profile-section">
        <template #header>
          <span>账号详情</span>
        </template>
        <el-descriptions :column="1" border>
          <el-descriptions-item label="注册时间">
            {{ authStore.user?.createdAt ? formatDate(authStore.user.createdAt) : '-' }}
          </el-descriptions-item>
          <el-descriptions-item label="账号状态">
            <el-tag :type="authStore.user?.isActive ? 'success' : 'danger'" size="small">
              {{ authStore.user?.isActive ? '活跃' : '已停用' }}
            </el-tag>
          </el-descriptions-item>
        </el-descriptions>
      </el-card>
    </div>
  </div>
</template>

<style scoped>
.profile-page {
  min-height: 100dvh;
  background: #f5f7fa;
  padding: 24px;
}

.profile-container {
  max-width: 900px;
  margin: 0 auto;
}

.profile-header {
  margin-bottom: 24px;
}

.profile-header h1 {
  margin: 12px 0 0;
  font-size: 22px;
  font-weight: 700;
  color: #303133;
}

.profile-section {
  margin-bottom: 20px;
}

.section-alert {
  margin-bottom: 16px;
}

@media (max-width: 768px) {
  .profile-page {
    padding: 12px;
  }
}
</style>
