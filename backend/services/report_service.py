"""
Report export service — Markdown & PDF generation.
Phase 8' / US6.

Generates well-formatted Markdown from a report's sections, citations, and
gap-notes, then optionally converts to PDF via WeasyPrint.
"""

from __future__ import annotations

import uuid

from backend.core.database import get_postgres_session
from backend.models.report import ResearchReport
from backend.utils.logging import get_logger

logger = get_logger(__name__)


async def export_markdown(report_id: uuid.UUID, user_id: uuid.UUID) -> str:
    """Generate a Markdown representation of a research report."""
    report = await _load_report(report_id, user_id)
    return _build_markdown(report)


async def export_pdf(report_id: uuid.UUID, user_id: uuid.UUID) -> bytes:
    """Generate a PDF from a research report (via Markdown → HTML → PDF)."""
    from backend.tools.exporter import html_to_pdf, markdown_to_html

    md = await export_markdown(report_id, user_id)
    return html_to_pdf(markdown_to_html(md))


async def export_report(
    report_id: uuid.UUID, user_id: uuid.UUID, fmt: str,
) -> tuple[bytes, str, str]:
    """Unified export — returns (content, mime_type, filename)."""
    import re
    report = await _load_report(report_id, user_id)
    safe_title = re.sub(r'[\\/*?:"<>|]', '-', report.title)[:80]

    if fmt == "markdown":
        # Build markdown from already-loaded report to avoid a second DB round-trip
        md = _build_markdown(report)
        return md.encode("utf-8"), "text/markdown; charset=utf-8", f"{safe_title}.md"
    elif fmt == "pdf":
        from backend.tools.exporter import markdown_to_pdf_bytes
        md = _build_markdown(report)
        pdf = markdown_to_pdf_bytes(md, title=report.title)
        return pdf, "application/pdf", f"{safe_title}.pdf"
    else:
        raise ValueError(f"不支持的导出格式: {fmt}")


# ── Internal ─────────────────────────────────────────────────────────


async def _load_report(report_id: uuid.UUID, user_id: uuid.UUID) -> ResearchReport:
    """Load a report scoped to the owning user."""
    session = get_postgres_session()
    async with session:
        # ResearchReport has no user_id — verify ownership via the linked task
        report = await session.get(ResearchReport, report_id)
        if report is None:
            raise ValueError("报告不存在")

        from backend.models.task import ResearchTask
        task = await session.get(ResearchTask, report.task_id)
        if task is None or task.user_id != user_id:
            raise ValueError("报告不存在")

        # Eager-load sections/citations (they're JSONB, already loaded)
        return report


def _build_markdown(report: ResearchReport) -> str:
    """Build Markdown text from an already-loaded report."""
    lines: list[str] = []
    lines.append(f"# {report.title}")
    lines.append("")
    lines.append(f"> {report.abstract}")
    lines.append("")

    for sec in report.sections_json or []:
        heading = sec.get("heading", "")
        content = sec.get("content", "")
        if heading:
            lines.append(f"## {heading}")
            lines.append("")
        if content:
            lines.append(content)
            lines.append("")

    if report.gap_notes:
        lines.append("## 知识缺口说明")
        lines.append("")
        lines.append(report.gap_notes)
        lines.append("")

    citations = report.citations_json or []
    if citations:
        lines.append("## 引用列表")
        lines.append("")
        for i, cite in enumerate(citations, 1):
            title = cite.get("title") or cite.get("text", "未知来源")
            url = cite.get("url", "")
            credibility = cite.get("credibility", "unknown")
            source_type = cite.get("sourceType", "")
            meta = [f"可信度: {credibility}"]
            if source_type:
                meta.append(f"类型: {source_type}")
            lines.append(f"{i}. **{title}**")
            if url:
                lines.append(f"   - URL: {url}")
            lines.append(f"   - {' | '.join(meta)}")
            lines.append("")

    return "\n".join(lines)
