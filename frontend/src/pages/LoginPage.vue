<script setup lang="ts">
import { ref, reactive } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import type { FormInstance, FormRules } from 'element-plus'

const router = useRouter()
const route = useRoute()
const authStore = useAuthStore()

const formRef = ref<FormInstance>()
const form = reactive({
  email: '',
  password: '',
})

const rules: FormRules = {
  email: [
    { required: true, message: '请输入邮箱地址', trigger: 'blur' },
    { type: 'email', message: '邮箱格式不正确', trigger: 'blur' },
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
  ],
}

const submitting = ref(false)
const serverError = ref('')

async function handleLogin() {
  const valid = formRef.value ? await formRef.value.validate().catch(() => false) : false
  if (!valid) return

  submitting.value = true
  serverError.value = ''
  try {
    await authStore.login(form.email, form.password)
    // Respect redirect param (validated to be same-origin path)
    const redirect = route.query.redirect as string | undefined
    if (redirect && redirect.startsWith('/') && !redirect.startsWith('//')) {
      router.push(redirect)
    } else {
      router.push('/chat')
    }
  } catch {
    serverError.value = authStore.error || '登录失败，请重试'
  } finally {
    submitting.value = false
    // Clear password from reactive state for DevTools hygiene
    form.password = ''
  }
}
</script>

<template>
  <div class="auth-page">
    <div class="auth-card auth-card--narrow">
      <h1 class="auth-title">Deep Research Platform</h1>
      <p class="auth-subtitle">深度研究平台 — 学术研究智能助手</p>

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
        @submit.prevent="handleLogin"
      >
        <el-form-item label="邮箱" prop="email">
          <el-input
            v-model="form.email"
            type="email"
            placeholder="输入邮箱地址"
            size="large"
            autocomplete="email"
          />
        </el-form-item>

        <el-form-item label="密码" prop="password">
          <el-input
            v-model="form.password"
            type="password"
            placeholder="输入密码"
            size="large"
            show-password
            autocomplete="current-password"
          />
        </el-form-item>

        <el-form-item>
          <el-button
            type="primary"
            size="large"
            class="auth-btn"
            :loading="submitting"
            :disabled="submitting"
            @click="handleLogin"
          >
            {{ submitting ? '登录中…' : '登录' }}
          </el-button>
        </el-form-item>
      </el-form>

      <div class="auth-footer">
        <span>还没有账号？</span>
        <router-link to="/register">立即注册</router-link>
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
.auth-card--narrow { max-width: 420px; }
.auth-card--wide   { max-width: 520px; }

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

@media (max-width: 480px) {
  .auth-card {
    padding: 24px;
  }
}
</style>
