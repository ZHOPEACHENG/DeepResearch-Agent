import { marked } from 'marked'

/** Render markdown to HTML.  GFM tables, strikethrough, and line-break
 *  conversion are enabled.  Citation markers [1], [2,3], [1-3] are
 *  converted into clickable span elements. */
export function renderMarkdown(text: string): string {
  if (!text) return ''
  try {
    let html = marked.parse(text, { breaks: true, gfm: true }) as string
    // Convert citation markers to clickable spans so the CitationPopup
    // can wire up click handlers via event delegation.
    html = html.replace(
      /\[(\d+(?:[-,]\d+)*)\]/g,
      '<span class="cite-marker" data-cite="$1">[$1]</span>',
    )
    return html
  } catch {
    return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  }
}
