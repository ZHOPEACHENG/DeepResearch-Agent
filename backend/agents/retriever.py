"""
Retriever agent — multi-source retrieval, dedup, storage (T052).

Runs searches across the configured ``SearchSource`` implementations,
deduplicates results by URL + title similarity, normalizes metadata, and
persists each round's results to MongoDB via ``task_service.store_stage_output``.
Also captures a raw content snapshot (T088) and carries the credibility
score computed by the search layer (T090).

The agent extracts keywords from ``research_plan`` and runs multi-source
parallel search. With the Analyzer ReAct loop handling gap detection
internally, the Retriever only runs once per pipeline.

Constitution II: the Retriever only retrieves + stores — it does not plan,
analyze, or write prose. It emits the ``retrieval_results`` contract.
Constitution V: it depends only on the ``SearchSource`` abstraction, never
on a concrete provider.
"""

from __future__ import annotations

import asyncio
import uuid
from difflib import SequenceMatcher
from typing import Any

from backend.agents.base import Agent
from backend.core.database import get_mongo_db
from backend.services import task_service
from backend.tools.search import (
    SearchResult,
    SearchSource,
    WebSearchSource,
    get_search_sources,
    normalize_url,
)
from backend.utils.datetime import now_iso
from backend.utils.logging import get_logger

logger = get_logger(__name__)

# How similar two titles must be to be considered duplicates.
_TITLE_DEDUP_THRESHOLD = 0.85
# Max results to keep per round after dedup.
_MAX_RESULTS_PER_ROUND = 20


class RetrieverAgent(Agent):
    """Execute multi-source search, deduplicate, and persist results."""

    name = "retriever"
    description = "多源检索、去重、归一化元数据并写入 MongoDB"

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        """
        Retrieve for the current round and store results in MongoDB.

        Reads:
            state["task_id"]         — owning task (str|UUID).
            state["conversation_id"] — optional, for Mongo scoping.
            state["research_plan"]   — used to derive search keywords.

        Writes:
            state["retrieval_results"] — list[dict] of normalized, persisted
              results (with their Mongo _id as ``result_id``).
            state["all_retrieval_results"] — cumulative list for the
              Analyzer/Writer citation mapping.

        Raises:
            ValueError: if task_id is missing.
        """
        task_id = state.get("task_id")
        if not task_id:
            logger.error("retriever_no_task_id")
            raise ValueError("缺少 task_id，无法执行检索")
        task_id_str = str(task_id)

        queries = self._extract_queries(state)
        if not queries:
            logger.warning(
                "retriever_no_queries",
                task_id=task_id_str,
            )
            state["retrieval_results"] = []
            return state

        source_types = self._source_types(state)
        sources = get_search_sources(source_types)

        logger.info(
            "retriever_started",
            task_id=task_id_str,
            user_id=state.get("user_id"),
            queries=len(queries),
            sources=[s.name for s in sources],
        )

        async def _search_source(source: SearchSource) -> tuple[str, list[SearchResult]]:
            """Search all queries on one source — sequential within, parallel across."""
            results: list[SearchResult] = []
            for query in queries:
                try:
                    r = await source.search(query, limit=8)
                    results.extend(r)
                except Exception:
                    logger.warning(
                        "retriever_source_query_failed",
                        task_id=task_id_str,
                        source=source.name,
                        query=query[:80],
                        exc_info=True,
                    )
            return source.name, results

        tasks = [_search_source(s) for s in sources]
        gathered_raw = await asyncio.gather(*tasks, return_exceptions=True)

        gathered: list[SearchResult] = []
        per_source_counts: dict[str, int] = {}
        for item in gathered_raw:
            if isinstance(item, Exception):
                logger.warning(
                    "retriever_source_crashed",
                    task_id=task_id_str,
                    error=str(item)[:200],
                )
                continue
            name, results = item
            gathered.extend(results)
            per_source_counts[name] = len(results)

        deduped = self._dedup(gathered)[:_MAX_RESULTS_PER_ROUND]

        # Enrich web results with full-text fetch + LLM summarization (round 1 only).
        enriched = await self._enrich_web_results(deduped, state)
        docs = self._to_documents(enriched, task_id_str, state)

        # Persist this round to MongoDB (task_service adds task_id).
        if docs:
            try:
                await task_service.store_stage_output(uuid.UUID(task_id_str), "retrieval", docs)
            except Exception:
                logger.error(
                    "retriever_store_failed",
                    task_id=task_id_str,
                    round=1,
                    exc_info=True,
                )
                raise

        # Resolve Mongo _ids back into the docs so downstream citation
        # mapping can reference them by retrieval_result_id.
        docs = await _attach_mongo_ids(docs, task_id_str)

        state["retrieval_results"] = docs
        # Maintain a cumulative view across rounds for the Analyzer/Writer.
        prior = list(state.get("all_retrieval_results") or [])
        prior.extend(docs)
        state["all_retrieval_results"] = prior

        logger.info(
            "retriever_completed",
            task_id=task_id_str,
            user_id=state.get("user_id"),
            gathered=len(gathered),
            deduped=len(deduped),
            per_source=per_source_counts,
        )
        return state

    # ── Helpers ────────────────────────────────────────────────────────

    async def _enrich_web_results(
        self, results: list[SearchResult], state: dict[str, Any],
    ) -> list[SearchResult]:
        """For web results, fetch full page text and LLM-summarize it.

        Only runs in round 1 (initial retrieval) — gap-fill rounds skip this
        step since they need speed over depth.
        """
        round_number = int(state.get("round_number", 1) or 1)
        if round_number != 1:
            return results

        web_results = [r for r in results if r.source_type == "web"]
        if not web_results:
            return results

        from backend.tools.llm import get_llm_provider

        web_source = WebSearchSource()
        summarization_provider = get_llm_provider("summarization")

        async def _fetch_and_summarize_one(r: SearchResult) -> SearchResult:
            full_text = await web_source.fetch_and_extract(r.url)
            if not full_text:
                return r  # keep original snippet
            try:
                summary = await summarization_provider.summarize_page(full_text)
                if summary and summary.strip():
                    r.abstract = summary.strip()
                    r.raw_snapshot = full_text[:5000]
            except Exception:
                logger.warning(
                    "page_summarize_failed", url=r.url[:120], exc_info=True,
                )
            return r

        enriched = await asyncio.gather(
            *[_fetch_and_summarize_one(r) for r in web_results],
            return_exceptions=True,
        )

        # Re-assemble: replace web results with enriched versions (or keep originals).
        out: list[SearchResult] = []
        for r in results:
            if r.source_type == "web":
                found = False
                for item in enriched:
                    if isinstance(item, Exception):
                        continue
                    if item.url == r.url:
                        out.append(item)
                        found = True
                        break
                if not found:
                    out.append(r)  # enrichment failed, keep original
            else:
                out.append(r)
        return out

    def _extract_queries(self, state: dict[str, Any]) -> list[str]:
        """Extract search queries from the research plan's keywords.

        Pure extraction — no LLM calls and no gap-fill heuristics. With the
        Analyzer ReAct loop handling gap detection internally, the Retriever
        only runs once per pipeline (round 1).
        """
        plan = state.get("research_plan") or {}
        keywords = plan.get("search_keywords") if isinstance(plan, dict) else None
        if isinstance(keywords, list):
            return [k["keyword"] for k in keywords if isinstance(k, dict) and k.get("keyword")]
        return []

    def _source_types(self, state: dict[str, Any]) -> list[str]:
        plan = state.get("research_plan") or {}
        expected = plan.get("expected_sources") if isinstance(plan, dict) else None
        if isinstance(expected, list) and expected:
            return [str(s) for s in expected]
        return None  # let get_search_sources use the default set

    def _dedup(self, results: list[SearchResult]) -> list[SearchResult]:
        """Deduplicate by normalized URL, then by title similarity."""
        seen_urls: set[str] = set()
        seen_titles: list[tuple[str, SearchResult]] = []
        out: list[SearchResult] = []
        for r in results:
            key = normalize_url(r.url) if r.url else ""
            if key and key in seen_urls:
                continue
            if key:
                seen_urls.add(key)
            # Title similarity check against kept titles.
            low_title = r.title.strip().lower()
            if low_title and any(
                SequenceMatcher(None, low_title, t).ratio() >= _TITLE_DEDUP_THRESHOLD
                for t, _ in seen_titles
            ):
                continue
            seen_titles.append((low_title, r))
            out.append(r)
        return out

    def _to_documents(
        self,
        results: list[SearchResult],
        task_id_str: str,
        state: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Convert SearchResults into Mongo-ready RetrievalResult documents.

        With the Analyzer ReAct loop handling gap detection internally, the
        Retriever only runs once per pipeline (round 1).
        """
        conv_id = state.get("conversation_id")
        docs: list[dict[str, Any]] = []
        for r in results:
            docs.append({
                "result_id": "",  # filled in after insert from Mongo _id
                "task_id": task_id_str,
                "conversation_id": str(conv_id) if conv_id else None,
                "round": 1,
                "round_number": 1,
                "retrieved_at": now_iso(),
                **r.to_dict(),
            })
        return docs


async def _attach_mongo_ids(
    docs: list[dict[str, Any]], task_id_str: str
) -> list[dict[str, Any]]:
    """Backfill ``result_id`` with the Mongo ObjectId string of each stored doc.

    Retrieval results are inserted without a stable client-side id; we look
    them up by (task_id, round, url+title) to recover the ObjectId so the
    Citation table and citation_map can reference a stable id. Best-effort:
    if a lookup fails the doc keeps an empty result_id and is still usable
    for synthesis (just not citable).
    """
    if not docs:
        return docs
    try:
        db = get_mongo_db()
        cursor = db["retrieval_results"].find(
            {"task_id": task_id_str, "round_number": 1}
        )
        stored = await cursor.to_list(length=len(docs) * 2)
        # Index by (url, title) for a fast match.
        by_key: dict[tuple[str, str], str] = {}
        for d in stored:
            url_key = (d.get("url") or "").strip().lower()
            title_key = (d.get("title") or "").strip().lower()
            by_key[(url_key, title_key)] = str(d["_id"])
        for doc in docs:
            key = ((doc.get("url") or "").strip().lower(), (doc.get("title") or "").strip().lower())
            oid = by_key.get(key)
            if oid:
                doc["result_id"] = oid
    except Exception:
        logger.warning(
            "retriever_attach_mongo_ids_failed",
            task_id=task_id_str,
            doc_count=len(docs),
            exc_info=True,
        )
    return docs
