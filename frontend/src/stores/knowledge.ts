/**
 * Pinia knowledge base store.
 *
 * Phase 7 / US5 — document list, upload/delete actions, search/ask state.
 */

import { defineStore } from 'pinia'
import { ref } from 'vue'
import type {
  DocumentItem,
  DocumentUploadResponse,
  SearchResultItem,
  AskSource,
} from '@/types/document'
import * as kbApi from '@/api/knowledge'
import { extractApiError } from '@/api/client'

export const useKnowledgeStore = defineStore('knowledge', () => {
  // ── Document list state ──────────────────────────────────────────
  const documents = ref<DocumentItem[]>([])
  const loading = ref(false)
  const uploading = ref(false)
  const error = ref<string | null>(null)
  const total = ref(0)
  const page = ref(1)

  // ── Search state ─────────────────────────────────────────────────
  const searchQuery = ref('')
  const searchResults = ref<SearchResultItem[]>([])
  const searchLoading = ref(false)
  const searchTotal = ref(0)
  const searchTookMs = ref<number | null>(null)

  // ── QA state ─────────────────────────────────────────────────────
  const askLoading = ref(false)
  const lastAnswer = ref('')
  const lastAnswerSources = ref<AskSource[]>([])
  const lastQuestion = ref('')

  // ── Polling state ────────────────────────────────────────────────
  let _pollTimer: ReturnType<typeof setInterval> | null = null

  // ── Actions ──────────────────────────────────────────────────────

  function clearError() {
    error.value = null
  }

  async function fetchDocuments(p?: number) {
    loading.value = true
    error.value = null
    try {
      const pg = p ?? page.value
      const data = await kbApi.fetchDocuments(pg)
      documents.value = data.items
      total.value = data.total
      page.value = data.page
    } catch (e: unknown) {
      error.value = extractApiError(e).detail
      console.error('[knowledge] fetchDocuments failed:', e)
    } finally {
      loading.value = false
    }
  }

  async function uploadDocument(file: File): Promise<DocumentUploadResponse> {
    uploading.value = true
    error.value = null
    try {
      const result = await kbApi.uploadDocument(file)
      // Prepend to list
      documents.value.unshift({
        id: result.id,
        userId: '',
        filename: result.filename,
        fileType: result.fileType as DocumentItem['fileType'],
        fileSizeBytes: result.fileSizeBytes,
        processingStatus: result.processingStatus as DocumentItem['processingStatus'],
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(),
      })
      total.value++
      // Start polling for status updates if still processing
      _startPollingIfNeeded()
      return result
    } catch (e: unknown) {
      error.value = extractApiError(e).detail
      console.error('[knowledge] uploadDocument failed:', e)
      throw e
    } finally {
      uploading.value = false
    }
  }

  async function deleteDocument(docId: string) {
    error.value = null
    try {
      await kbApi.deleteDocument(docId)
      documents.value = documents.value.filter(d => d.id !== docId)
      total.value = Math.max(0, total.value - 1)
    } catch (e: unknown) {
      error.value = extractApiError(e).detail
      console.error('[knowledge] deleteDocument failed:', e)
      throw e
    }
  }

  async function search(q: string) {
    searchQuery.value = q
    searchLoading.value = true
    error.value = null
    try {
      const data = await kbApi.searchKnowledge(q)
      searchResults.value = data.results
      searchTotal.value = data.total
      searchTookMs.value = data.tookMs ?? null
    } catch (e: unknown) {
      error.value = extractApiError(e).detail
      console.error('[knowledge] search failed:', e)
    } finally {
      searchLoading.value = false
    }
  }

  async function askQuestion(question: string, topK = 5) {
    askLoading.value = true
    error.value = null
    lastQuestion.value = question
    try {
      const data = await kbApi.askQuestion({ question, topK })
      lastAnswer.value = data.answer
      lastAnswerSources.value = data.sources
    } catch (e: unknown) {
      error.value = extractApiError(e).detail
      console.error('[knowledge] askQuestion failed:', e)
    } finally {
      askLoading.value = false
    }
  }

  function clearSearch() {
    searchQuery.value = ''
    searchResults.value = []
    searchTotal.value = 0
    searchTookMs.value = null
  }

  function clearQA() {
    lastAnswer.value = ''
    lastAnswerSources.value = []
    lastQuestion.value = ''
  }

  function clearAll() {
    documents.value = []
    loading.value = false
    uploading.value = false
    error.value = null
    total.value = 0
    page.value = 1
    clearSearch()
    clearQA()
    _stopPolling()
  }

  // ── Internal polling ─────────────────────────────────────────────

  function _startPollingIfNeeded() {
    // Check if any document is still processing
    const hasProcessing = documents.value.some(
      d => d.processingStatus === 'pending' || d.processingStatus === 'processing',
    )
    if (!hasProcessing) return
    if (_pollTimer) return // already polling

    _pollTimer = setInterval(async () => {
      const stillProcessing = documents.value.some(
        d => d.processingStatus === 'pending' || d.processingStatus === 'processing',
      )
      if (!stillProcessing) {
        _stopPolling()
        return
      }
      // Refresh document list to get updated statuses
      try {
        await fetchDocuments(page.value)
      } catch {
        // silent — polling is best-effort
      }
    }, 5000) // poll every 5s
  }

  function _stopPolling() {
    if (_pollTimer) {
      clearInterval(_pollTimer)
      _pollTimer = null
    }
  }

  return {
    documents, loading, uploading, error, total, page,
    searchQuery, searchResults, searchLoading, searchTotal, searchTookMs,
    askLoading, lastAnswer, lastAnswerSources, lastQuestion,
    clearError, clearAll, fetchDocuments, uploadDocument, deleteDocument,
    search, askQuestion, clearSearch, clearQA,
  }
})
