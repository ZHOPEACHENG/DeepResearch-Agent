export type MessageRole = 'user' | 'assistant' | 'system' | 'tool'

export type MessageType = 'text' | 'plan_card' | 'retrieval_card' | 'clarifying_question'
  | 'report_card' | 'citation' | 'error' | 'gap_question' | 'analysis_card'

export type PlanAction = 'accept' | 'modify' | 'reject'

export interface ConversationSummary {
  id: string
  title: string
  model: string
  tags: string[]
  messageCount: number
  lastMessagePreview: string | null
  createdAt: string
  updatedAt: string
}

export interface MessageDetail {
  id: string
  conversationId: string
  role: MessageRole
  content: string
  messageType: MessageType
  parentMessageId: string | null
  metadata: Record<string, unknown>
  tokenCount: number
  model?: string
  createdAt: string
}

export interface ConversationListResponse {
  items: ConversationSummary[]
  total: number
  page: number
  pageSize: number
}

export interface MessageListResponse {
  items: MessageDetail[]
  hasMore: boolean
}

export type ChatMode = 'chat' | 'research'

export type SSEEventType = 'message_created' | 'chat_chunk'
  | 'plan_generated' | 'plan_action' | 'phase_change' | 'progress'
  | 'retrieval_started' | 'retrieval_progress' | 'retrieval_complete'
  | 'analysis_complete' | 'gap_question' | 'clarity_question'
  | 'report_chunk' | 'report_complete'
  | 'complete' | 'error' | 'done'

export interface SSEEvent {
  event: SSEEventType
  data: Record<string, unknown>
}
