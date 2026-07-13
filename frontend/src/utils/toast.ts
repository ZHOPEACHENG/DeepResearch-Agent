/**
 * Toast notification utility — thin wrapper around Element Plus ElMessage
 * with consistent formatting and automatic error boundary for API failures.
 *
 * T122: Centralized toast system — use this for all user-facing notifications.
 * Existing ElMessage calls continue to work independently.
 */

import { ElMessage, ElNotification } from 'element-plus'
import type { MessageHandler } from 'element-plus'

const TOAST_DURATION = 3000
const ERROR_DURATION = 5000

/** Show a success toast (auto-dismiss). */
export function toastSuccess(message: string): MessageHandler {
  return ElMessage({ message, type: 'success', duration: TOAST_DURATION })
}

/** Show an error toast with longer duration. */
export function toastError(message: string): MessageHandler {
  return ElMessage({ message, type: 'error', duration: ERROR_DURATION })
}

/** Show a warning toast. */
export function toastWarning(message: string): MessageHandler {
  return ElMessage({ message, type: 'warning', duration: TOAST_DURATION })
}

/** Show an info toast. */
export function toastInfo(message: string): MessageHandler {
  return ElMessage({ message, type: 'info', duration: TOAST_DURATION })
}

/**
 * Centralized API error handler.
 *
 * Extracts a human-readable message from any Axios/network error shape
 * and displays it as a toast. Use this as a catch-all in API call chains.
 *
 * Known error shapes handled:
 *   - axios error with response.data.detail
 *   - axios error with response.status
 *   - network error (no response)
 *   - plain Error with message
 */
export function handleApiError(err: unknown, fallback = '操作失败，请重试'): void {
  if (err && typeof err === 'object') {
    const e = err as Record<string, unknown>

    // Axios error with server response
    if (e.response && typeof e.response === 'object') {
      const resp = e.response as Record<string, unknown>
      const data = (resp.data as Record<string, unknown> | undefined)
      if (data?.detail && typeof data.detail === 'string') {
        toastError(data.detail)
        return
      }
      if (resp.status === 429) {
        toastWarning('请求过于频繁，请稍后重试')
        return
      }
      if (resp.status && Number(resp.status) >= 500) {
        toastError('服务器错误，请稍后重试')
        return
      }
    }

    // Network error (no response)
    if (e.code === 'ERR_NETWORK' || e.message === 'Network Error') {
      toastError('网络连接失败，请检查网络后重试')
      return
    }

    // Plain error message
    if (e.message && typeof e.message === 'string') {
      toastError(e.message)
      return
    }
  }

  // Fallback
  toastError(fallback)
}

/**
 * Show a persistent notification (stays until user dismisses).
 * Use sparingly — for important alerts only.
 */
export function notifyPersistent(title: string, message: string, type: 'success' | 'warning' | 'error' | 'info' = 'info'): void {
  ElNotification({ title, message, type, duration: 0 })
}

/**
 * Default export — object form for tree-shakeable imports.
 */
export const toast = {
  success: toastSuccess,
  error: toastError,
  warning: toastWarning,
  info: toastInfo,
  apiError: handleApiError,
  notify: notifyPersistent,
}

export default toast
