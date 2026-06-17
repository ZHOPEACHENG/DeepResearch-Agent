/**
 * Auth API client — registration, login, token refresh, logout, profile.
 *
 * All write endpoints use apiClient (auto-attaches Bearer token).
 * register/login/refresh are public (no token needed).
 */

import apiClient, { setTokens, clearTokens } from './client'
import type {
  LoginRequest,
  RegisterRequest,
  TokenPair,
  User,
  UserUpdateRequest,
  ChangePasswordRequest,
} from '@/types/user'

// ── Public Endpoints ──────────────────────────────────────────────────

export async function register(req: RegisterRequest): Promise<TokenPair> {
  const { data } = await apiClient.post<TokenPair>('/auth/register', req)
  setTokens(data.accessToken, data.refreshToken)
  return data
}

export async function login(req: LoginRequest): Promise<TokenPair> {
  const { data } = await apiClient.post<TokenPair>('/auth/login', req)
  setTokens(data.accessToken, data.refreshToken)
  return data
}

export async function refreshToken(refreshTokenValue: string): Promise<TokenPair> {
  const { data } = await apiClient.post<TokenPair>('/auth/refresh', {
    refresh_token: refreshTokenValue,
  })
  setTokens(data.accessToken, data.refreshToken)
  return data
}

/**
 * Log out the current user.
 *
 * Clears tokens from localStorage. The caller (e.g. auth store or component)
 * is responsible for navigating to the login page — this function only
 * handles the API call + token cleanup, not navigation.
 */
export async function logout(): Promise<void> {
  try {
    await apiClient.post('/auth/logout')
  } finally {
    clearTokens()
  }
}

// ── Authenticated Endpoints ───────────────────────────────────────────

export async function getProfile(): Promise<User> {
  const { data } = await apiClient.get<User>('/users/me')
  return data
}

export async function updateProfile(req: UserUpdateRequest): Promise<User> {
  const { data } = await apiClient.patch<User>('/users/me', req)
  return data
}

export async function changePassword(req: ChangePasswordRequest): Promise<void> {
  await apiClient.put('/users/me/password', req)
}
