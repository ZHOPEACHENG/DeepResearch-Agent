import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type {
  ConversationSummary, MessageDetail, SSEEvent, PlanAction,
} from '@/types/conversation'
import * as convApi from '@/api/conversations'
import { extractApiError } from '@/api/client'

/** UUID v4 fallback for environments without crypto.randomUUID() */
function generateUUID(): string {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return crypto.randomUUID()
  }
  // Fallback for non-secure contexts
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0
    const v = c === 'x' ? r : (r & 0x3) | 0x8
    return v.toString(16)
  })
}

export const useConversationStore = defineStore('conversations', () => {
  const conversations = ref<ConversationSummary[]>([])
  const currentConversation = ref<ConversationSummary | null>(null)
  const messages = ref<MessageDetail[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)
  const isStreaming = ref(false)
  const streamingContent = ref('')
  const streamingIntent = ref<string | null>(null)
  const selectedModel = ref<string>('gpt-4o')
  const availableModels = ref<string[]>([])
  const total = ref(0)
  const page = ref(1)

  let _abortController: AbortController | null = null

  const hasConversations = computed(() => conversations.value.length > 0)

  function clearError() { error.value = null }

  async function fetchConversations(p?: number) {
    loading.value = true
    error.value = null
    try {
      const data = await convApi.fetchConversations(p ?? page.value)
      conversations.value = data.items
      total.value = data.total
      page.value = data.page
    } catch (e: unknown) {
      const apiErr = extractApiError(e)
      error.value = apiErr.detail
      console.error('[store] fetchConversations failed:', e)
    } finally {
      loading.value = false
    }
  }

  async function createConversation(title?: string) {
    error.value = null
    try {
      const detail = await convApi.createConversation(title, selectedModel.value)
      conversations.value.unshift(detail)
      return detail
    } catch (e: unknown) {
      const apiErr = extractApiError(e)
      error.value = apiErr.detail
      console.error('[store] createConversation failed:', e)
      throw e
    }
  }

  async function fetchConversation(convId: string) {
    // Abort any in-flight SSE stream from previous conversation
    _abortController?.abort()
    _abortController = null
    // Reset streaming state so thinking indicator disappears
    isStreaming.value = false
    streamingContent.value = ''
    streamingIntent.value = null

    loading.value = true
    error.value = null
    // Clear current conversation to trigger loading UI
    currentConversation.value = null
    messages.value = []

    try {
      const [conv, msgResult] = await Promise.all([
        convApi.fetchConversation(convId),
        convApi.fetchMessages(convId),
      ])
      currentConversation.value = conv
      messages.value = msgResult.items
    } catch (e: unknown) {
      const apiErr = extractApiError(e)
      error.value = apiErr.detail
      console.error('[store] fetchConversation failed:', e)
    } finally {
      loading.value = false
    }
  }

  function sendMessage(convId: string, content: string, parentMessageId?: string) {
    // Abort any existing stream before starting a new one
    _abortController?.abort()
    _abortController = null

    isStreaming.value = true
    streamingContent.value = ''
    streamingIntent.value = null
    error.value = null
    let userMessageId: string | null = null

    _abortController = convApi.sendMessageStream(
      convId, content, parentMessageId ?? null, selectedModel.value,
      (event: SSEEvent) => {
        switch (event.event) {
          case 'message_created':
            messages.value.push({
              id: (event.data.messageId as string) || generateUUID(),
              conversationId: convId,
              role: 'user',
              content: content,
              messageType: 'text',
              parentMessageId: parentMessageId || null,
              metadata: {},
              tokenCount: 0,
              model: selectedModel.value,
              createdAt: new Date().toISOString(),
            })
            userMessageId = messages.value[messages.value.length - 1].id
            break

          case 'chat_chunk':
            streamingContent.value += (event.data.content as string) || ''
            break

          case 'intent_classified':
            streamingIntent.value = event.data.intent as string
            break

          case 'plan_generated':
            messages.value.push({
              id: (event.data.messageId as string) || generateUUID(),
              conversationId: convId,
              role: 'assistant',
              content: '',
              messageType: 'plan_card',
              parentMessageId: parentMessageId || null,
              metadata: event.data as Record<string, unknown>,
              tokenCount: 0,
              model: (event.data.model as string) || selectedModel.value,
              createdAt: new Date().toISOString(),
            })
            break

          // report_complete is a Phase 4' event (not yet emitted by backend).
          // Handler present for forward compatibility.
          case 'report_complete':
            messages.value.push({
              id: (event.data.messageId as string) || generateUUID(),
              conversationId: convId,
              role: 'assistant',
              content: '',
              messageType: 'report_card',
              parentMessageId: null,
              metadata: event.data as Record<string, unknown>,
              tokenCount: 0,
              model: (event.data.model as string) || selectedModel.value,
              createdAt: new Date().toISOString(),
            })
            break

          case 'error':
            error.value = (event.data.message as string) || '发生未知错误'
            isStreaming.value = false
            console.error('[store] SSE error event:', event.data)
            break

          case 'done':
            if (streamingContent.value) {
              messages.value.push({
                id: (event.data.messageId as string) || generateUUID(),
                conversationId: convId,
                role: 'assistant',
                content: streamingContent.value,
                messageType: 'text',
                parentMessageId: userMessageId,
                metadata: {},
                tokenCount: 0,
                model: selectedModel.value,
                createdAt: new Date().toISOString(),
              })
              streamingContent.value = ''
            }
            break

          default:
            // Future SSE event types (retrieval_started, analysis_complete, etc.)
            // Log for observability; no UI action needed yet.
            if (import.meta.env.DEV) {
              console.debug('[store] unhandled SSE event:', event.event, event.data)
            }
            break
        }
      },
      (err: Error) => {
        error.value = err.message || '流式传输失败'
        isStreaming.value = false
        streamingContent.value = ''
        console.error('[store] sendMessage stream error:', err)
      },
      () => {
        isStreaming.value = false
        streamingContent.value = ''
        // Update the conversation list to reflect the new message
        fetchConversations()
      },
    )
  }

  function stopStreaming() {
    _abortController?.abort()
    _abortController = null
    isStreaming.value = false
    streamingContent.value = ''
    streamingIntent.value = null
  }

  function clearCurrentConversation() {
    _abortController?.abort()
    _abortController = null
    isStreaming.value = false
    currentConversation.value = null
    messages.value = []
    streamingContent.value = ''
    streamingIntent.value = null
    error.value = null
  }

  function clearAll() {
    _abortController?.abort()
    _abortController = null
    isStreaming.value = false
    currentConversation.value = null
    messages.value = []
    conversations.value = []
    streamingContent.value = ''
    streamingIntent.value = null
    error.value = null
    loading.value = false
  }

  async function actOnPlan(messageId: string, action: PlanAction, modifications?: string) {
    error.value = null
    try {
      await convApi.actOnPlan(messageId, action, modifications)
    } catch (e: unknown) {
      const apiErr = extractApiError(e)
      error.value = apiErr.detail
      console.error('[store] actOnPlan failed:', e)
      throw e
    }
  }

  async function deleteConversation(convId: string) {
    error.value = null
    try {
      await convApi.deleteConversation(convId)
      conversations.value = conversations.value.filter(c => c.id !== convId)
      if (currentConversation.value?.id === convId) {
        currentConversation.value = null
        messages.value = []
      }
    } catch (e: unknown) {
      const apiErr = extractApiError(e)
      error.value = apiErr.detail
      console.error('[store] deleteConversation failed:', e)
      throw e
    }
  }

  async function fetchAvailableModels() {
    try {
      const models = await convApi.fetchAvailableModels()
      availableModels.value = models
      if (!selectedModel.value || !models.includes(selectedModel.value)) {
        selectedModel.value = models[0] || 'gpt-4o'
      }
    } catch (e: unknown) {
      console.error('[store] fetchAvailableModels failed:', e)
      // Keep current model selection; availableModels stays empty until API succeeds
    }
  }

  function setSelectedModel(model: string) {
    selectedModel.value = model
  }

  return {
    conversations, currentConversation, messages, loading, error,
    isStreaming, streamingContent, streamingIntent, selectedModel,
    availableModels, total, page,
    hasConversations,
    clearError, clearAll, fetchConversations, createConversation, fetchConversation,
    sendMessage, stopStreaming, clearCurrentConversation,
    actOnPlan, deleteConversation,
    fetchAvailableModels, setSelectedModel,
  }
})
