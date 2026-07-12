/**
 * Knowledge base API client.
 *
 * Phase 7 / US5 — document upload, search, and QA API calls.
 */

import apiClient from './client'
import type {
  DocumentList,
  DocumentItem,
  DocumentUploadResponse,
  SearchResults,
  AskRequest,
  AskResponse,
} from '@/types/document'

// ── Document CRUD ────────────────────────────────────────────────────

export async function uploadDocument(file: File): Promise<DocumentUploadResponse> {
  const formData = new FormData()
  formData.append('file', file)

  const { data } = await apiClient.post<DocumentUploadResponse>(
    '/knowledge/documents',
    formData,
    {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 60000, // 60s for larger uploads
    },
  )
  return data
}

export async function fetchDocuments(
  page = 1,
  pageSize = 20,
): Promise<DocumentList> {
  const { data } = await apiClient.get<DocumentList>('/knowledge/documents', {
    params: { page, pageSize },
  })
  return data
}

export async function fetchDocument(documentId: string): Promise<DocumentItem> {
  const { data } = await apiClient.get<DocumentItem>(
    `/knowledge/documents/${documentId}`,
  )
  return data
}

export async function deleteDocument(documentId: string): Promise<void> {
  await apiClient.delete(`/knowledge/documents/${documentId}`)
}

export interface DocumentContent {
  document_id: string
  filename: string
  chunks: Array<{ chunkIndex: number; text: string }>
}

export async function fetchDocumentContent(
  documentId: string,
): Promise<DocumentContent> {
  const { data } = await apiClient.get<DocumentContent>(
    `/knowledge/documents/${documentId}/content`,
  )
  return data
}

// ── Search & QA ──────────────────────────────────────────────────────

export async function searchKnowledge(
  q: string,
  page = 1,
  pageSize = 20,
): Promise<SearchResults> {
  const { data } = await apiClient.get<SearchResults>('/knowledge/search', {
    params: { q, page, pageSize },
  })
  return data
}

export async function askQuestion(body: AskRequest): Promise<AskResponse> {
  const { data } = await apiClient.post<AskResponse>('/knowledge/ask', body, {
    timeout: 60000, // 60s for LLM response
  })
  return data
}
