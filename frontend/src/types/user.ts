/**
 * TypeScript type definitions for User-related entities.
 */

export interface User {
  id: string
  username: string
  email: string
  display_name: string | null
  institution: string | null
  is_active: boolean
  created_at: string
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
  access_token: string
  refresh_token: string
  token_type: string
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
