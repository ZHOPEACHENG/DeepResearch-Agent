import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { ElMessage } from 'element-plus'
import type {
  ConversationSummary, MessageDetail, SSEEvent, PlanAction, ChatMode,
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
  const selectedModel = ref<string>('gpt-4o')
  const availableModels = ref<string[]>([])
  const total = ref(0)
  const page = ref(1)
  // User-selected Deep Research toggle: 'chat' (default) or 'research'.
  // Persists across sends until the user changes it — no auto-reset.
  const mode = ref<ChatMode>('chat')
  const deepThinking = ref(false)
  // Current research phase label shown while the pipeline runs
  // (e.g. "正在检索资料"). Empty when not in a research phase.
  const phaseLabel = ref('')

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
    error.value = null
    phaseLabel.value = ''
    let userMessageId: string | null = null

    _abortController = convApi.sendMessageStream(
      convId, content, parentMessageId ?? null, selectedModel.value, mode.value, deepThinking.value,
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

          case 'plan_generated': {
            // A revised plan reuses the same messageId — update the existing
            // plan_card in place instead of stacking a new one.
            const revisedId = (event.data.messageId as string) || ''
            const existingPlan = revisedId
              ? messages.value.find(m => m.id === revisedId && m.messageType === 'plan_card')
              : undefined
            if (existingPlan) {
              existingPlan.metadata = {
                ...existingPlan.metadata,
                ...event.data as Record<string, unknown>,
              }
              // Refresh the rendered plan text too (back-end refills plan_text
              // into the card metadata; mirror it onto content for the <pre>).
              if (event.data.planText) {
                existingPlan.content = event.data.planText as string
              }
              break
            }
            messages.value.push({
              id: revisedId || generateUUID(),
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
          }

          // The user accepted / modified / rejected the plan. Update the
          // pending plan card's status so its action buttons disappear.
          case 'plan_action': {
            const action = (event.data.action as string) || ''
            const taskId = (event.data.taskId as string) || ''
            const outlet = (event.data.modify_outlet as string) || ''
            const planMsg = [...messages.value]
              .reverse()
              .find(m => m.messageType === 'plan_card'
                && (m.metadata?.taskId === taskId || m.metadata?.status === 'pending_confirmation'))
            if (planMsg) {
              // revise outlet already set the card to 'revised' via
              // plan_generated — don't overwrite it. augment outlet marks
              // 'accepted_with_notes' so the card shows the user's focus.
              let nextStatus: string
              if (action === 'accept') {
                nextStatus = 'accepted'
              } else if (action === 'modify') {
                nextStatus = outlet === 'revise' ? 'revised' : 'accepted_with_notes'
              } else {
                nextStatus = 'rejected'
              }
              // Only advance the status forward; skip if the revise outlet
              // already established 'revised' (avoid clobbering with itself).
              if (nextStatus !== 'revised' || planMsg.metadata?.status !== 'revised') {
                planMsg.metadata = {
                  ...planMsg.metadata,
                  status: nextStatus,
                }
              }
              // Augment outlet: carry the user's focus notes into the card so
              // the tag renders them without needing a reload.
              if (event.data.user_focus_notes) {
                planMsg.metadata = {
                  ...planMsg.metadata,
                  user_focus_notes: event.data.user_focus_notes as string,
                }
              }
            }
            break
          }

          // Phase progress labels (retrieve / analyze / synthesize / write).
          case 'phase_change':
            phaseLabel.value = (event.data.message as string) || ''
            break

          case 'progress':
            // Lightweight progress tick; surface as the phase label.
            if (event.data.message) phaseLabel.value = event.data.message as string
            break

          // Retrieval finished — backend has already persisted a
          // retrieval_card message; mirror it in the live message list.
          case 'retrieval_complete':
            messages.value.push({
              id: (event.data.messageId as string) || generateUUID(),
              conversationId: convId,
              role: 'assistant',
              content: '',
              messageType: 'retrieval_card',
              parentMessageId: parentMessageId || null,
              metadata: event.data as Record<string, unknown>,
              tokenCount: 0,
              model: selectedModel.value,
              createdAt: new Date().toISOString(),
            })
            break

          // Analysis finished — no dedicated card; update the phase label.
          case 'analysis_complete':
            phaseLabel.value = '分析完成，检测知识缺口'
            break

          case 'gap_question':
            // Gap question card persisted by backend via _mirror_pipeline_event.
            // Frontend renders it inline within the message list.
            if (event.data.messageId) {
              messages.value.push({
                id: event.data.messageId as string,
                conversationId: convId,
                role: 'assistant',
                content: '',
                messageType: 'gap_question',
                parentMessageId: parentMessageId || null,
                metadata: event.data as Record<string, unknown>,
                tokenCount: 0,
                createdAt: new Date().toISOString(),
              })
            }
            phaseLabel.value = `检测到 ${(event.data.gaps as any[])?.length || 0} 个关键知识缺口`
            break

          // Final report — backend persisted a report_card message.
          case 'report_complete':
            messages.value.push({
              id: (event.data.messageId as string) || generateUUID(),
              conversationId: convId,
              role: 'assistant',
              content: (event.data.abstract as string) || '',
              messageType: 'report_card',
              parentMessageId: parentMessageId || null,
              metadata: event.data as Record<string, unknown>,
              tokenCount: 0,
              model: (event.data.model as string) || selectedModel.value,
              createdAt: new Date().toISOString(),
            })
            break

          // Pipeline finished cleanly.
          case 'complete':
            phaseLabel.value = '研究完成'
            break

          case 'error':
            error.value = (event.data.message as string) || '发生未知错误'
            isStreaming.value = false
            if ((event.data.code as string) === 'THINKING_CONFLICT') {
              deepThinking.value = false
              ElMessage.warning({
                message: '深度思考模式与结构化输出不兼容，已自动关闭深度思考开关，请重试',
                duration: 5000,
              })
            }
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
            phaseLabel.value = ''
            break

          default:
            // Future SSE event types — log for observability, no UI action.
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
    phaseLabel.value = ''
  }

  function clearCurrentConversation() {
    _abortController?.abort()
    _abortController = null
    isStreaming.value = false
    currentConversation.value = null
    messages.value = []
    streamingContent.value = ''
    phaseLabel.value = ''
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
    phaseLabel.value = ''
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
    isStreaming, streamingContent, selectedModel,
    availableModels, total, page,
    mode, deepThinking, phaseLabel,
    hasConversations,
    clearError, clearAll, fetchConversations, createConversation, fetchConversation,
    sendMessage, stopStreaming, clearCurrentConversation,
    actOnPlan, deleteConversation,
    fetchAvailableModels, setSelectedModel,
  }
})
