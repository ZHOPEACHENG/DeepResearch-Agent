/**
 * Axios instance with base URL, JWT interceptor, and error handling.
 *
 * Features:
 * - Automatic Authorization header injection from localStorage
 * - 401 response → automatic token refresh → retry
 * - Centralized error handling with structured error extraction
 */

import axios, { type AxiosError, type InternalAxiosRequestConfig } from 'axios'

// ── Token Storage Keys (exported for use across the app) ──────────────

export const ACCESS_TOKEN_KEY = 'access_token'
export const REFRESH_TOKEN_KEY = 'refresh_token'

export function getAccessToken(): string | null {
  return localStorage.getItem(ACCESS_TOKEN_KEY)
}

export function getRefreshToken(): string | null {
  return localStorage.getItem(REFRESH_TOKEN_KEY)
}

export function setTokens(accessToken: string, refreshToken: string): void {
  localStorage.setItem(ACCESS_TOKEN_KEY, accessToken)
  localStorage.setItem(REFRESH_TOKEN_KEY, refreshToken)
}

export function clearTokens(): void {
  localStorage.removeItem(ACCESS_TOKEN_KEY)
  localStorage.removeItem(REFRESH_TOKEN_KEY)
}

// ── Cross-tab token sync ──────────────────────────────────────────────

if (typeof window !== 'undefined') {
  window.addEventListener('storage', (e) => {
    if (e.key === ACCESS_TOKEN_KEY && !e.newValue) {
      // Token was cleared in another tab — redirect to login
      window.location.href = '/login'
    }
    if (e.key === REFRESH_TOKEN_KEY && !e.newValue) {
      clearTokens()
      window.location.href = '/login'
    }
  })
}

// ── Axios Instance ──────────────────────────────────────────────────

const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1',
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
})

// ── Request Interceptor ─────────────────────────────────────────────

apiClient.interceptors.request.use(
  (config: InternalAxiosRequestConfig) => {
    const token = getAccessToken()
    if (token && config.headers) {
      config.headers.Authorization = `Bearer ${token}`
    }
    return config
  },
  (error: AxiosError) => Promise.reject(error),
)

// ── Response Interceptor (401 → refresh → retry) ────────────────────

let isRefreshing = false
let failedQueue: Array<{
  resolve: (token: string) => void
  reject: (error: unknown) => void
}> = []

function processQueue(error: unknown, token: string | null = null): void {
  failedQueue.forEach(({ resolve, reject }) => {
    if (error) {
      reject(error)
    } else if (token) {
      resolve(token)
    }
  })
  failedQueue = []
}

apiClient.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const originalRequest = error.config as InternalAxiosRequestConfig & {
      _retry?: boolean
    }

    // Only attempt refresh on 401 and if we haven't already retried
    if (error.response?.status !== 401 || originalRequest._retry) {
      return Promise.reject(error)
    }

    // If already refreshing, queue this request
    if (isRefreshing) {
      return new Promise<string>((resolve, reject) => {
        failedQueue.push({ resolve, reject })
      })
        .then((token) => {
          if (originalRequest.headers) {
            originalRequest.headers.Authorization = `Bearer ${token}`
          }
          return apiClient(originalRequest)
        })
    }

    originalRequest._retry = true
    isRefreshing = true

    const refreshToken = getRefreshToken()
    if (!refreshToken) {
      // No refresh token available — drain queue and reject all
      processQueue(new Error('会话已过期，请重新登录'), null)
      clearTokens()
      isRefreshing = false
      return Promise.reject(error)
    }

    try {
      const response = await axios.post(
        `${apiClient.defaults.baseURL}/auth/refresh`,
        { refresh_token: refreshToken },
      )
      const { accessToken, refreshToken: newRefreshToken } = response.data
      setTokens(accessToken, newRefreshToken)

      processQueue(null, accessToken)

      if (originalRequest.headers) {
        originalRequest.headers.Authorization = `Bearer ${accessToken}`
      }
      return apiClient(originalRequest)
    } catch (refreshError) {
      processQueue(refreshError, null)
      clearTokens()
      // Redirect to login — use router navigation if available, fallback to hard redirect
      window.location.href = '/login'
      return Promise.reject(refreshError)
    } finally {
      isRefreshing = false
    }
  },
)

// ── Typed Error Extraction ──────────────────────────────────────────

export interface ApiError {
  detail: string
  statusCode: number
  type?: string
}

export function extractApiError(error: unknown): ApiError {
  if (axios.isAxiosError(error) && error.response?.data) {
    return {
      detail: error.response.data.detail || '发生未知错误，请稍后重试',
      statusCode: error.response.status,
      type: error.response.data.type,
    }
  }
  return {
    detail: '网络连接失败，请检查网络后重试',
    statusCode: 0,
  }
}

export default apiClient
