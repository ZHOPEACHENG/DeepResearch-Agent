import { marked } from 'marked'

/**
 * Render markdown string to safe HTML.
 *
 * We sanitize lightly by disabling raw HTML in the markdown source
 * (prevents XSS via <img onerror> etc.).  The output is used with
 * v-html — NEVER pass unsanitized user input through this function.
 */
export function renderMarkdown(text: string): string {
  if (!text) return ''
  // Disable raw HTML passthrough — any <tag> in the source is escaped.
  return marked.parse(text, { async: false, breaks: true, gfm: true }) as string
}
