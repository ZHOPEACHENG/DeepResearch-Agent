/**
 * TypeScript type definitions for User-related entities.
 */

export interface User {
  id: string
  username: string
  email: string
  displayName: string | null
  institution: string | null
  isActive: boolean
  createdAt: string
}

export interface LoginRequest {
  email: string
  password: string
}

export interface RegisterRequest {
  username: string
  email: string
  password: string
  display_name?: string
  institution?: string
}

export interface TokenPair {
  accessToken: string
  refreshToken: string
  tokenType: string
}

export interface UserUpdateRequest {
  display_name?: string | null
  institution?: string | null
  email?: string | null
}

export interface ChangePasswordRequest {
  old_password: string
  new_password: string
}
