<script setup lang="ts">
import { ref, reactive } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { createPasswordValidator, createConfirmPasswordValidator } from '@/utils/validators'
import type { FormInstance, FormRules } from 'element-plus'

const router = useRouter()
const route = useRoute()
const authStore = useAuthStore()

const formRef = ref<FormInstance>()
const form = reactive({
  username: '',
  email: '',
  password: '',
  confirmPassword: '',
  displayName: '',
  institution: '',
})

const rules: FormRules = {
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    { min: 3, max: 50, message: '用户名长度 3–50 个字符', trigger: 'blur' },
    { pattern: /^[a-zA-Z0-9_-]+$/, message: '仅支持字母、数字、下划线和连字符', trigger: 'blur' },
  ],
  email: [
    { required: true, message: '请输入邮箱地址', trigger: 'blur' },
    { type: 'email', message: '邮箱格式不正确', trigger: 'blur' },
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 8, message: '密码至少 8 个字符', trigger: 'blur' },
    { validator: createPasswordValidator(), trigger: 'blur' },
  ],
  confirmPassword: [
    { required: true, message: '请确认密码', trigger: 'blur' },
    { validator: createConfirmPasswordValidator(() => form.password), trigger: 'blur' },
  ],
}

const submitting = ref(false)
const serverError = ref('')

async function handleRegister() {
  const valid = formRef.value ? await formRef.value.validate().catch(() => false) : false
  if (!valid) return

  submitting.value = true
  serverError.value = ''
  try {
    await authStore.register(
      form.username,
      form.email,
      form.password,
      form.displayName || undefined,
      form.institution || undefined,
    )
    const redirect = route.query.redirect as string | undefined
    if (redirect && redirect.startsWith('/') && !redirect.startsWith('//')) {
      router.push(redirect)
    } else {
      router.push('/chat')
    }
  } catch {
    serverError.value = authStore.error || '注册失败，请重试'
  } finally {
    submitting.value = false
    form.password = ''
    form.confirmPassword = ''
  }
}
</script>

<template>
  <div class="auth-page">
    <div class="auth-card auth-card--wide">
      <h1 class="auth-title">创建账号</h1>
      <p class="auth-subtitle">注册 Deep Research Platform 账号</p>

      <el-alert
        v-if="serverError"
        :title="serverError"
        type="error"
        show-icon
        closable
        class="auth-alert"
        @close="serverError = ''"
      />

      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-position="top"
        @submit.prevent="handleRegister"
      >
        <el-form-item label="用户名" prop="username">
          <el-input
            v-model="form.username"
            placeholder="3–50 个字符，字母/数字/下划线/连字符"
            size="large"
            autocomplete="username"
          />
        </el-form-item>

        <el-form-item label="邮箱" prop="email">
          <el-input
            v-model="form.email"
            type="email"
            placeholder="输入邮箱地址"
            size="large"
            autocomplete="email"
          />
        </el-form-item>

        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="显示名称" prop="displayName">
              <el-input
                v-model="form.displayName"
                placeholder="可选"
                size="large"
              />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="所属机构" prop="institution">
              <el-input
                v-model="form.institution"
                placeholder="可选"
                size="large"
              />
            </el-form-item>
          </el-col>
        </el-row>

        <el-form-item label="密码" prop="password">
          <el-input
            v-model="form.password"
            type="password"
            placeholder="至少 8 个字符，含大小写字母和数字"
            size="large"
            show-password
            autocomplete="new-password"
          />
        </el-form-item>

        <el-form-item label="确认密码" prop="confirmPassword">
          <el-input
            v-model="form.confirmPassword"
            type="password"
            placeholder="再次输入密码"
            size="large"
            show-password
            autocomplete="new-password"
          />
        </el-form-item>

        <el-form-item>
          <el-button
            type="primary"
            size="large"
            class="auth-btn"
            :loading="submitting"
            :disabled="submitting"
            @click="handleRegister"
          >
            {{ submitting ? '注册中…' : '注册' }}
          </el-button>
        </el-form-item>
      </el-form>

      <div class="auth-footer">
        <span>已有账号？</span>
        <router-link to="/login">立即登录</router-link>
      </div>
    </div>
  </div>
</template>

<style scoped>
.auth-page {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
  padding: 16px;
}

.auth-card {
  width: 100%;
  padding: 40px;
  background: #fff;
  border-radius: 12px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.08);
}
.auth-card--wide { max-width: 520px; }

.auth-title {
  margin: 0 0 4px;
  font-size: 24px;
  font-weight: 700;
  text-align: center;
  color: #303133;
}

.auth-subtitle {
  margin: 0 0 28px;
  font-size: 14px;
  text-align: center;
  color: #909399;
}

.auth-alert {
  margin-bottom: 16px;
}

.auth-btn {
  width: 100%;
}

.auth-footer {
  margin-top: 16px;
  text-align: center;
  font-size: 14px;
  color: #909399;
}

.auth-footer a {
  color: #409eff;
  text-decoration: none;
  margin-left: 4px;
}
.auth-footer a:hover { text-decoration: underline; }

@media (max-width: 560px) {
  .auth-card {
    padding: 24px;
  }
}
</style>
