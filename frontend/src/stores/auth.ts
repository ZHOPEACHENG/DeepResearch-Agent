/**
 * Pinia auth store — login, logout, register, token persistence, user state.
 *
 * Tokens are persisted in localStorage (via @/api/client helper).
 * On app launch, call store.init() to restore the session from stored tokens.
 */

import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { User } from '@/types/user'
import * as authApi from '@/api/auth'
import { extractApiError, clearTokens, getAccessToken } from '@/api/client'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<User | null>(null)
  const isAuthenticated = ref(false)
  const loading = ref(false)
  const error = ref<string | null>(null)

  // ── Getters ──────────────────────────────────────────────────────────

  const isLoggedIn = computed(() => isAuthenticated.value && user.value !== null)
  const userName = computed(() => user.value?.displayName || user.value?.username || 'User')

  // ── Actions ──────────────────────────────────────────────────────────

  function clearError() {
    error.value = null
  }

  /**
   * Initialize auth state from persisted tokens on app launch.
   * Attempts to load the user profile — if it fails, clears tokens.
   *
   * Must be called once after app mount (e.g. in main.ts or App.vue).
   */
  async function init(): Promise<boolean> {
    const token = getAccessToken()
    if (!token) return false

    loading.value = true
    error.value = null
    try {
      user.value = await authApi.getProfile()
      isAuthenticated.value = true
      return true
    } catch (e: unknown) {
      // Token expired or invalid — clear state silently
      clearTokens()
      user.value = null
      isAuthenticated.value = false
      return false
    } finally {
      loading.value = false
    }
  }

  async function login(email: string, password: string): Promise<void> {
    loading.value = true
    error.value = null
    try {
      await authApi.login({ email, password })
      // Fetch profile after successful login — only set isAuthenticated on success
      user.value = await authApi.getProfile()
      isAuthenticated.value = true
    } catch (e: unknown) {
      const apiErr = extractApiError(e)
      error.value = apiErr.detail
      throw e
    } finally {
      loading.value = false
    }
  }

  async function register(
    username: string,
    email: string,
    password: string,
    displayName?: string,
    institution?: string,
  ): Promise<void> {
    loading.value = true
    error.value = null
    try {
      await authApi.register({
        username,
        email,
        password,
        display_name: displayName,
        institution,
      })
      // Fetch profile after successful registration — only set isAuthenticated on success
      user.value = await authApi.getProfile()
      isAuthenticated.value = true
    } catch (e: unknown) {
      const apiErr = extractApiError(e)
      error.value = apiErr.detail
      throw e
    } finally {
      loading.value = false
    }
  }

  async function logoutUser(): Promise<void> {
    loading.value = true
    try {
      await authApi.logout()
    } catch {
      // Even if the API call fails, clear local state
    } finally {
      user.value = null
      isAuthenticated.value = false
      loading.value = false
    }
  }

  async function fetchProfile(): Promise<void> {
    loading.value = true
    error.value = null
    try {
      user.value = await authApi.getProfile()
      isAuthenticated.value = true
    } catch (e: unknown) {
      // If we can't fetch profile, the session is invalid
      const apiErr = extractApiError(e)
      error.value = apiErr.detail
      clearTokens()
      user.value = null
      isAuthenticated.value = false
    } finally {
      loading.value = false
    }
  }

  async function updateProfile(updates: {
    displayName?: string | null
    institution?: string | null
    email?: string | null
  }): Promise<void> {
    loading.value = true
    error.value = null
    try {
      user.value = await authApi.updateProfile({
        display_name: updates.displayName,
        institution: updates.institution,
        email: updates.email,
      })
    } catch (e: unknown) {
      const apiErr = extractApiError(e)
      error.value = apiErr.detail
      throw e
    } finally {
      loading.value = false
    }
  }

  async function changePassword(oldPassword: string, newPassword: string): Promise<void> {
    loading.value = true
    error.value = null
    try {
      await authApi.changePassword({
        old_password: oldPassword,
        new_password: newPassword,
      })
    } catch (e: unknown) {
      const apiErr = extractApiError(e)
      error.value = apiErr.detail
      throw e
    } finally {
      loading.value = false
    }
  }

  return {
    user,
    isAuthenticated,
    loading,
    error,
    isLoggedIn,
    userName,
    clearError,
    init,
    login,
    register,
    logoutUser,
    fetchProfile,
    updateProfile,
    changePassword,
  }
})
