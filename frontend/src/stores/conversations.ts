import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type {
  ConversationSummary, MessageDetail, SSEEvent,
} from '@/types/conversation'
import * as convApi from '@/api/conversations'

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
  const availableModels = ref<string[]>(['gpt-4o', 'gpt-4o-mini', 'claude-sonnet-4-6', 'claude-opus-4-8'])
  const total = ref(0)
  const page = ref(1)

  let _abortController: AbortController | null = null

  const hasConversations = computed(() => conversations.value.length > 0)

  function clearError() { error.value = null }

  async function fetchConversations(p?: number) {
    loading.value = true
    try {
      const data = await convApi.fetchConversations(p ?? page.value)
      conversations.value = data.items
      total.value = data.total
      page.value = data.page
    } catch (e: any) {
      error.value = e?.response?.data?.detail || 'Failed to load conversations'
      console.error('[store] fetchConversations failed:', e)
    } finally {
      loading.value = false
    }
  }

  async function createConversation(title?: string) {
    try {
      const detail = await convApi.createConversation(title)
      conversations.value.unshift(detail)
      return detail
    } catch (e: any) {
      error.value = e?.response?.data?.detail || 'Failed to create conversation'
      console.error('[store] createConversation failed:', e)
      throw e
    }
  }

  async function fetchConversation(convId: string) {
    loading.value = true
    try {
      const [conv, msgResult] = await Promise.all([
        convApi.fetchConversation(convId),
        convApi.fetchMessages(convId),
      ])
      currentConversation.value = conv
      messages.value = msgResult.items
    } catch (e: any) {
      error.value = e?.response?.data?.detail || 'Failed to load conversation'
      console.error('[store] fetchConversation failed:', e)
    } finally {
      loading.value = false
    }
  }

  function sendMessage(convId: string, content: string, parentMessageId?: string) {
    isStreaming.value = true
    streamingContent.value = ''
    streamingIntent.value = null
    error.value = null

    _abortController = convApi.sendMessageStream(
      convId, content, parentMessageId, selectedModel.value,
      (event: SSEEvent) => {
        switch (event.event) {
          case 'message_created':
            break
          case 'chat_chunk':
            streamingContent.value += (event.data.content as string) || ''
            break
          case 'intent_classified':
            streamingIntent.value = event.data.intent as string
            break
          case 'plan_generated':
            messages.value.push({
              id: event.data.messageId as string,
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
          case 'report_complete':
            messages.value.push({
              id: event.data.messageId as string,
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
            error.value = event.data.message as string || 'An error occurred'
            console.error('[store] SSE error event:', event.data)
            break
          case 'done':
            if (streamingContent.value) {
              messages.value.push({
                id: crypto.randomUUID(),
                conversationId: convId,
                role: 'assistant',
                content: streamingContent.value,
                messageType: 'text',
                parentMessageId: null,
                metadata: {},
                tokenCount: 0,
                model: selectedModel.value,
                createdAt: new Date().toISOString(),
              })
              streamingContent.value = ''
            }
            break
        }
      },
      (err: Error) => {
        error.value = err.message
        isStreaming.value = false
        console.error('[store] sendMessage stream error:', err)
      },
      () => {
        isStreaming.value = false
        streamingContent.value = ''
        fetchConversations()
      },
    )
  }

  function stopStreaming() {
    _abortController?.abort()
    isStreaming.value = false
  }

  async function actOnPlan(messageId: string, action: string, modifications?: string) {
    try {
      await convApi.actOnPlan(messageId, action, modifications)
    } catch (e: any) {
      error.value = e?.response?.data?.detail || 'Failed to act on plan'
      console.error('[store] actOnPlan failed:', e)
      throw e
    }
  }

  async function deleteConversation(convId: string) {
    try {
      await convApi.deleteConversation(convId)
      conversations.value = conversations.value.filter(c => c.id !== convId)
      if (currentConversation.value?.id === convId) {
        currentConversation.value = null
        messages.value = []
      }
    } catch (e: any) {
      error.value = e?.response?.data?.detail || 'Failed to delete conversation'
      console.error('[store] deleteConversation failed:', e)
      throw e
    }
  }

  async function fetchAvailableModels() {
    try {
      const models = await convApi.fetchAvailableModels()
      availableModels.value = models
      if (!selectedModel.value || !models.includes(selectedModel.value)) {
        selectedModel.value = models[0]
      }
    } catch (e: any) {
      console.error('[store] fetchAvailableModels failed:', e)
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
    clearError, fetchConversations, createConversation, fetchConversation,
    sendMessage, stopStreaming, actOnPlan, deleteConversation,
    fetchAvailableModels, setSelectedModel,
  }
})
