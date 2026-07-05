"""
Writer — report generation + citation annotation.

Turns the synthesized knowledge base into a structured research report
(abstract + background + sectioned body keyed by research question + gap
notes + full citation list), with every factual claim annotated by a
citation index. Persists the report to PostgreSQL (ResearchReport) and
writes one Citation row per cited source.

Constitution I (Research Credibility First): the prompt enforces inline
citation markers and a complete citation list; the persisted Citation rows
link each [N] back to a retrieval_result_id so claims trace to sources.
Constitution II: the Writer only writes prose + citations — it does not
synthesize or retrieve.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from backend.agents.base import Agent
from backend.core.database import get_postgres_session
from backend.tools.llm import get_chat_model
from backend.models.report import Citation, ResearchReport
from backend.schemas.llm_outputs import WriterOutput
from backend.utils.logging import get_logger

logger = get_logger(__name__)

_WRITER_SYSTEM = """You are an academic research-report writer.
You receive a synthesized knowledge base, the research questions, and a numbered
citation list mapping index → source id. Write a rigorous research report.

Structure:
- title: a concise, descriptive report title
- abstract: 150-300 word abstract summarising the findings
- background: 1-2 paragraphs framing the research topic
- sections: one per core research question, each with a heading and
  Markdown body citing sources inline as [1] or [2,3]
- gap_notes: what remains unanswered or uncertain, and why

Rules:
- Every factual statement MUST carry an inline citation marker [N] referring
  to the citation list provided.
- Only use citation indices that exist in the provided list. Never fabricate
  references.
- One section per core research question where possible.
- citation_indices lists the indices actually used in that section."""


class WriterAgent(Agent):
    """Generate a cited, structured research report and persist it."""

    name = "writer"
    description = "生成含引用标注的结构化研究报告并写入 PostgreSQL"

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        """
        Write the report from ``synthesized_knowledge`` and persist it.

        Reads:
            state["task_id"]
            state["research_plan"]          — question tree for section headings.
            state["synthesized_knowledge"]  — merged knowledge base + citation_map.
            state["all_retrieval_results"]  — sources, used to build the citation list.

        Writes:
            state["final_report"] — dict with report_id, title, abstract,
              sections, citations, gap_notes (matching the ReportCard payload).
        """
        task_id = state.get("task_id")
        if not task_id:
            logger.error("writer_no_task_id")
            raise ValueError("缺少 task_id，无法生成报告")
        task_id_str = str(task_id)
        task_uuid = uuid.UUID(task_id_str)

        synth = state.get("synthesized_knowledge") or {}
        results = list(state.get("all_retrieval_results") or [])
        questions = self._question_headings(state.get("research_plan") or {})
        user_focus_notes = (state.get("user_focus_notes") or "").strip()

        # Build the numbered citation list: index → retrieval_result_id + meta.
        citation_list, id_to_index = self._build_citation_list(results)

        logger.info(
            "writer_started",
            task_id=task_id_str,
            sources=len(results),
            citations=len(citation_list),
            questions=len(questions),
        )

        model = get_chat_model("writer", temperature=0.3, max_tokens=6000)
        structured = model.with_structured_output(WriterOutput, method="json_schema")
        messages = [
            {"role": "system", "content": _WRITER_SYSTEM},
            {"role": "user", "content": self._build_prompt(
                synth, questions, citation_list, user_focus_notes,
            )},
        ]

        try:
            output: WriterOutput = await structured.ainvoke(messages)
        except Exception:
            logger.error("writer_llm_failed", task_id=task_id_str, exc_info=True)
            raise

        report = self._assemble_report(output, synth, citation_list, id_to_index)

        # Persist to PostgreSQL (report + citation rows).
        report_id = await self._persist(task_uuid, task_id_str, report, id_to_index)
        report["report_id"] = str(report_id)
        report["id"] = str(report_id)
        report["task_id"] = task_id_str

        state["final_report"] = report
        logger.info(
            "writer_completed",
            task_id=task_id_str,
            report_id=str(report_id),
            sections=len(report["sections"]),
            citations=len(report["citations"]),
        )
        return state

    # ── Helpers ────────────────────────────────────────────────────────

    def _question_headings(self, plan: dict[str, Any]) -> list[str]:
        out: list[str] = []
        for q in plan.get("research_questions", []) if isinstance(plan, dict) else []:
            if isinstance(q, dict) and (q.get("question") or "").strip():
                out.append(q["question"].strip())
        return out

    def _build_citation_list(
        self, results: list[dict]
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        """Assign a 1-based citation index to each unique source.

        Returns (citation_list, id_to_index). Sources without a retrievable
        result_id are skipped from the citation list (they cannot be cited
        back to a stable record), but remain visible in the knowledge base.
        """
        citation_list: list[dict[str, Any]] = []
        id_to_index: dict[str, int] = {}
        for r in results:
            rid = (r.get("result_id") or "").strip()
            if not rid or rid in id_to_index:
                continue
            idx = len(citation_list) + 1
            id_to_index[rid] = idx
            citation_list.append({
                "index": idx,
                "retrieval_result_id": rid,
                "title": (r.get("title") or "").strip(),
                "url": (r.get("url") or "").strip(),
                "source_type": r.get("source_type", ""),
                "authors": r.get("authors") or [],
                "publication_date": r.get("publication_date"),
                "doi": r.get("doi"),
                "credibility": r.get("credibility", "unknown"),
                "text": self._citation_text(r),
            })
        return citation_list, id_to_index

    def _citation_text(self, r: dict) -> str:
        authors = ", ".join(r.get("authors") or [])
        year = (r.get("publication_date") or "")[:4]
        title = (r.get("title") or "").strip()
        url = (r.get("url") or "").strip()
        doi = r.get("doi")
        parts = [p for p in (authors, year, title) if p]
        text = ". ".join(parts)
        if doi:
            text += f". doi:{doi}"
        elif url:
            text += f". {url}"
        return text + "."

    def _build_prompt(
        self,
        synth: dict,
        questions: list[str],
        citation_list: list[dict],
        user_focus_notes: str = "",
    ) -> str:
        content = (synth.get("synthesized_content") or "").strip()
        open_qs = synth.get("open_questions") or []
        open_block = "\n".join(f"- {q}" for q in open_qs) or "(none)"
        q_block = "\n".join(f"- {q}" for q in questions) or "(derive headings from the content)"
        cite_block = "\n".join(
            f"[{c['index']}] {c['text']}  (source_id={c['retrieval_result_id']})"
            for c in citation_list
        ) or "(no sources available — write a report that explicitly states the lack of sources)"
        # Augment outlet (B-plan): the user asked to pay extra attention to
        # something without changing the plan's direction. Surface it so the
        # report reflects that emphasis. Empty = no user focus (accept path).
        focus_block = (
            f"用户特别要求关注（在报告相应章节侧重体现）:\n{user_focus_notes}\n\n"
            if user_focus_notes and user_focus_notes.strip()
            else ""
        )
        return (
            f"{focus_block}"
            f"Research questions to address as sections:\n{q_block}\n\n"
            f"Synthesized knowledge base:\n{content or '(empty)'}\n\n"
            f"Unresolved / open questions to fold into gap_notes:\n{open_block}\n\n"
            f"Citation list (use these indices inline as [N]):\n{cite_block}\n\n"
            "Write the research report now."
        )

    def _assemble_report(
        self,
        output: WriterOutput,
        synth: dict,
        citation_list: list[dict],
        id_to_index: dict[str, int],
    ) -> dict[str, Any]:
        """Convert typed ``WriterOutput`` to the canonical report dict shape.

        Preserves the exact same output structure as the pre-LangChain version
        so SSE events and PostgreSQL persistence remain compatible.
        """
        sections: list[dict[str, Any]] = []
        for s in output.sections:
            sections.append({
                "heading": s.heading or "未命名章节",
                "content": s.content,
                "citations": [int(i) for i in s.citation_indices if isinstance(i, (int, float))],
            })

        # If the model produced no sections, fold the knowledge content into
        # a single section so the report is never empty.
        if not sections:
            sections.append({
                "heading": "研究内容",
                "content": (synth.get("synthesized_content") or "（无可用内容）"),
                "citations": [],
            })

        # Ensure a background section exists up front.
        if output.background:
            sections.insert(0, {
                "heading": "研究背景",
                "content": output.background,
                "citations": [],
            })

        # The persisted citation list is the full numbered list (even if not
        # every index is referenced — completeness for traceability).
        citations = [
            {
                "index": c["index"],
                "text": c["text"],
                "sourceRef": c["retrieval_result_id"],
                "title": c["title"],
                "url": c["url"],
                "sourceType": c["source_type"],
                "authors": c["authors"],
                "publicationDate": c["publication_date"],
                "doi": c["doi"],
                "credibility": c["credibility"],
            }
            for c in citation_list
        ]

        return {
            "title": output.title or "研究报告",
            "abstract": output.abstract,
            "sections": sections,
            "citations": citations,
            "gap_notes": output.gap_notes,
        }

    async def _persist(
        self,
        task_uuid: uuid.UUID,
        task_id_str: str,
        report: dict[str, Any],
        id_to_index: dict[str, int],
    ) -> uuid.UUID:
        """Insert the ResearchReport row + Citation rows, idempotent on task.

        If a report already exists for this task (e.g. a resumed run), it is
        replaced so the latest synthesis wins.
        """
        session = get_postgres_session()
        async with session:
            # Replace any existing report for this task.
            existing = await session.execute(
                select(ResearchReport).where(ResearchReport.task_id == task_uuid)
            )
            old = existing.scalar_one_or_none()
            if old is not None:
                await session.delete(old)
                await session.flush()

            row = ResearchReport(
                task_id=task_uuid,
                title=report["title"][:500],
                abstract=report["abstract"],
                sections_json=report["sections"],
                citations_json=report["citations"],
                gap_notes=report["gap_notes"] or None,
            )
            session.add(row)
            await session.flush()  # populate row.id

            for c in report["citations"]:
                rid = c.get("sourceRef") or ""
                # Citation.retrieval_result_id is String(24) — Mongo ObjectId hex.
                # Truncate defensively to the column width.
                session.add(Citation(
                    report_id=row.id,
                    index_number=int(c["index"]),
                    retrieval_result_id=rid[:24],
                    context_in_report=None,
                ))

            await session.commit()
            report_id = row.id

        logger.info(
            "report_persisted",
            task_id=task_id_str,
            report_id=str(report_id),
            citations=len(report["citations"]),
        )
        return report_id
