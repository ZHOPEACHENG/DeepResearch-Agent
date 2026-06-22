/**
 * TypeScript types for Report, ReportSection, and Citation.
 *
 * Used by ReportCard rendering and the report_complete SSE event payload.
 * Property names use camelCase (API convention); mirrors the ReportDetail
 * schema in contracts/api-v1.yaml.
 */

export type SourceType = 'web' | 'arxiv' | 'semantic_scholar' | 'knowledge_base'

export type Credibility = 'high' | 'medium' | 'low' | 'unknown'

export type GapSeverity = 'critical' | 'moderate' | 'minor'

/** A single section of a research report (keyed by a research question). */
export interface ReportSection {
  heading: string
  /** Markdown body, may contain inline citation markers [1], [2,3]. */
  content: string
  /** Citation indices referenced in this section. */
  citations: number[]
}

/** A single citation entry in the report's citation list. */
export interface ReportCitation {
  index: number
  /** Full formatted citation text. */
  text: string
  /** MongoDB RetrievalResult id this citation points back to. */
  sourceRef: string
  title: string
  url: string
  sourceType: SourceType | string
  authors: string[]
  publicationDate: string | null
  doi: string | null
  credibility: Credibility | string
}

/** A complete research report. */
export interface ResearchReport {
  id?: string
  reportId?: string
  taskId: string
  title: string
  abstract: string
  sections: ReportSection[]
  citations: ReportCitation[]
  gapNotes: string
}

/** Compact source shown in a retrieval_card. */
export interface RetrievalSource {
  title: string
  url: string
  sourceType: SourceType | string
  credibility: Credibility | string
}

/** Payload for the retrieval_complete SSE event. */
export interface RetrievalCompleteData {
  taskId: string
  round: number
  sourceCount: number
  roundCount: number
  sources: RetrievalSource[]
  messageId?: string
}

/** Payload for the report_complete SSE event. */
export interface ReportCompleteData {
  messageId?: string
  taskId: string
  reportId?: string
  model?: string
  title: string
  abstract: string
  sections: ReportSection[]
  citations: ReportCitation[]
  gapNotes: string
}
