/**
 * Knowledge Base type definitions.
 *
 * Phase 7 / US5 — document upload, search, and QA types.
 */

// ── Document ──────────────────────────────────────────────────────────

export interface DocumentItem {
  id: string
  userId: string
  filename: string
  fileType: 'pdf' | 'docx' | 'txt' | 'md'
  fileSizeBytes: number
  processingStatus: 'pending' | 'processing' | 'completed' | 'failed'
  processingError?: string | null
  createdAt: string
  updatedAt: string
  processedAt?: string | null
}

export interface DocumentList {
  items: DocumentItem[]
  total: number
  page: number
  pageSize: number
}

export interface DocumentUploadResponse {
  id: string
  filename: string
  fileType: string
  fileSizeBytes: number
  processingStatus: string
  message: string
}

// ── Search ────────────────────────────────────────────────────────────

export interface SearchResultItem {
  documentId: string
  filename: string
  chunkIndex: number
  text: string
  pageNumber?: number | null
  score?: number | null
  highlights: string[]
}

export interface SearchResults {
  query: string
  results: SearchResultItem[]
  total: number
  tookMs?: number | null
}

// ── QA ────────────────────────────────────────────────────────────────

export interface AskRequest {
  question: string
  topK?: number
}

export interface AskSource {
  documentId: string
  filename: string
  chunkIndex: number
  excerpt: string
  pageNumber?: number | null
}

export interface AskResponse {
  question: string
  answer: string
  sources: AskSource[]
  model: string
}

// ── Upload ────────────────────────────────────────────────────────────

export const VALID_FILE_TYPES = ['.pdf', '.docx', '.txt', '.md']
export const MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024 // 50 MB
