import apiClient, { getAccessToken } from './client'
import type {
  ConversationSummary, ConversationListResponse,
  MessageListResponse, SSEEvent, PlanAction, ChatMode,
} from '@/types/conversation'

export async function fetchAvailableModels(): Promise<string[]> {
  const { data } = await apiClient.get<{ models: string[] }>('/models')
  return data.models
}

export async function fetchConversations(
  page = 1, pageSize = 20, search?: string,
): Promise<ConversationListResponse> {
  const { data } = await apiClient.get<ConversationListResponse>('/conversations', {
    params: { page, page_size: pageSize, search },
  })
  return data
}

export async function createConversation(
  title?: string, model?: string,
): Promise<ConversationSummary> {
  const { data } = await apiClient.post<ConversationSummary>(
    '/conversations', { title, model },
  )
  return data
}

export async function fetchConversation(convId: string): Promise<ConversationSummary> {
  const { data } = await apiClient.get<ConversationSummary>(`/conversations/${convId}`)
  return data
}

export async function fetchMessages(
  convId: string, beforeId?: string, limit = 50,
): Promise<MessageListResponse> {
  const { data } = await apiClient.get<MessageListResponse>(
    `/conversations/${convId}/messages`,
    { params: { before_id: beforeId, limit } },
  )
  return data
}

export async function deleteConversation(convId: string): Promise<void> {
  await apiClient.delete(`/conversations/${convId}`)
}

export async function updateConversationTitle(
  convId: string, title: string,
): Promise<ConversationSummary> {
  const { data } = await apiClient.patch<ConversationSummary>(
    `/conversations/${convId}`, { title },
  )
  return data
}

export async function actOnPlan(
  messageId: string, action: PlanAction, modifications?: string,
): Promise<{ status: string; action: string }> {
  const { data } = await apiClient.post<{ status: string; action: string }>(
    `/conversations/messages/${messageId}/plan-action`,
    { action, modifications },
  )
  return data
}

export async function actOnGap(
  taskId: string, action: 'answer' | 'skip', conversationId: string,
): Promise<{ status: string; action: string }> {
  const { data } = await apiClient.post<{ status: string; action: string }>(
    `/conversations/research/${taskId}/gap-action`,
    { action, conversation_id: conversationId },
  )
  return data
}

/**
 * SSE streaming — send a message and receive streaming events.
 *
 * Uses native fetch() because axios does not support streaming responses.
 * Token is read once at stream start; if it expires mid-stream the
 * connection will break. For long research tasks, the backend should
 * complete within the access token TTL (30 min).
 */
export function sendMessageStream(
  convId: string,
  content: string,
  parentMessageId: string | null,
  model: string | undefined,
  mode: ChatMode,
  deepThinking: boolean,
  onEvent: (event: SSEEvent) => void,
  onError: (error: Error) => void,
  onDone: () => void,
): AbortController {
  const controller = new AbortController()
  const token = getAccessToken()
  const baseUrl = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1'

  fetch(`${baseUrl}/conversations/${convId}/messages`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': token ? `Bearer ${token}` : '',
      'Accept': 'text/event-stream',
    },
    body: JSON.stringify({ content, parent_message_id: parentMessageId, model, mode, deep_thinking: deepThinking }),
    signal: controller.signal,
  }).then(async (response) => {
    if (!response.ok) throw new Error(`请求失败 (HTTP ${response.status})`)
    const reader = response.body?.getReader()
    if (!reader) throw new Error('服务器未返回数据流')
    const decoder = new TextDecoder()
    let buffer = ''

    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''
        let eventType = ''
        for (const line of lines) {
          if (line.startsWith('event: ')) {
            eventType = line.slice(7).trim()
          } else if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.slice(6))
              onEvent({ event: eventType as SSEEvent['event'], data })
            } catch {
              if (import.meta.env.DEV) {
                console.warn('[conversations api] SSE parse error:', line.slice(0, 80))
              }
            }
          }
        }
      }
    } finally {
      reader.releaseLock()
    }
    onDone()
  }).catch((err: unknown) => {
    if (err instanceof Error && err.name !== 'AbortError') {
      console.error('[conversations api] SSE stream failed:', err)
      onError(err instanceof Error ? err : new Error(String(err)))
    }
  })

  return controller
}
