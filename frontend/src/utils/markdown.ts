import { marked } from 'marked'

/** Render markdown to HTML.  GFM tables, strikethrough, and line-break
 *  conversion are enabled.  The output is used with v-html — NEVER pass
 *  untrusted user input directly. */
export function renderMarkdown(text: string): string {
  if (!text) return ''
  try {
    return marked.parse(text, { breaks: true, gfm: true }) as string
  } catch {
    // Fall back to escaped plain text if markdown parsing fails
    return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  }
}
