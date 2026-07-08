import apiClient from './client'

export interface CitationDetail {
  index: number
  text: string
  sourceRef: string
  credibility: 'high' | 'medium' | 'low' | 'unknown'
  source: {
    title: string
    sourceType?: string
    url?: string
    abstract?: string
    authors?: string[]
    publicationDate?: string
    credibility?: string
    rawContentAvailable?: boolean
  }
}

/** Fetch full citation detail for a single report citation. */
export async function getCitationDetail(
  reportId: string, index: number,
): Promise<CitationDetail> {
  const { data } = await apiClient.get<CitationDetail>(
    `/reports/${reportId}/citations/${index}`,
  )
  return data
}
