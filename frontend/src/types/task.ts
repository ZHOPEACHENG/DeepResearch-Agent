/**
 * TypeScript type definitions for Task, StageOutput, and related entities.
 * All property names use camelCase (API convention).
 */

// ── Enums ──────────────────────────────────────────────────────────────

export type TaskStatus = 'pending' | 'running' | 'paused' | 'completed' | 'failed'

export type TaskPhase = 'planning' | 'retrieving' | 'analyzing' | 'synthesizing' | 'writing'

export type StageName = 'plan' | 'retrieval' | 'summary' | 'gaps' | 'report'

export type GapSeverity = 'critical' | 'moderate' | 'minor'

// ── Request Types ──────────────────────────────────────────────────────

export interface TaskCreateRequest {
  topic: string
  config?: TaskConfig
}

export interface TaskConfig {
  maxGapRounds?: number
  searchSources?: string[]
  language?: string
}

// ── Response Types ─────────────────────────────────────────────────────

/** Lightweight task summary for list views. Maps to TaskStatusRead (Python). */
export interface TaskSummary {
  id: string
  topic: string
  status: TaskStatus
  currentPhase: TaskPhase | null
  progressPct: number
  progressMessage: string | null
  elapsedSeconds: number
  tags: string[]
  createdAt: string
  updatedAt: string
  completedAt: string | null
}

/** Full task detail. Maps to TaskRead (Python). */
export interface TaskDetail extends TaskSummary {
  userId: string
  errorMessage: string | null
  retryCount: number
  configJson: Record<string, unknown>
}

/** Paginated task list. Maps to TaskListResponse (Python). */
export interface TaskListResponse {
  items: TaskSummary[]
  total: number
  page: number
  pageSize: number
}

// ── Stage Output Types ─────────────────────────────────────────────────

export interface ResearchQuestion {
  id: string
  text: string
  priority: number
  subQuestions: ResearchQuestion[]
}

export interface ResearchPlan {
  taskId: string
  topic: string
  questions: ResearchQuestion[]
  searchKeywords: string[]
  priorityOrder: string[]
  generatedAt: string | null
}

export interface RetrievalResult {
  resultId: string
  taskId: string
  roundNumber: number
  title: string
  abstract: string
  excerpt: string
  sourceUrl: string
  doi: string | null
  sourceType: string
  authors: string[]
  publicationDate: string | null
  credibility: string
  snapshotText: string
  retrievedAt: string | null
}

export interface KnowledgeSummary {
  taskId: string
  phase: string
  summaryContent: string
  citationMap: Record<string, string>
  generatedAt: string | null
}

export interface KnowledgeGap {
  taskId: string
  gapId: string
  description: string
  relatedQuestionId: string
  severity: GapSeverity
  triggeredRetrieval: boolean
  retrievalStatus: string
  createdAt: string | null
}

export interface StageOutputs {
  taskId: string
  plan: ResearchPlan | null
  retrievalRounds: RetrievalResult[][]
  knowledgeSummaries: KnowledgeSummary[]
  knowledgeGaps: KnowledgeGap[]
}
