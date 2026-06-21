"""
Search source abstraction and concrete implementations.

Defines the ``SearchSource`` interface (Constitution V — Extensible
Architecture) and three OpenAI-free, httpx-based providers:

- ``WebSearchSource``        — general web search via the Tavily API.
- ``ArxivSearchSource``      — arXiv paper search via the Atom export API.
- ``SemanticScholarSearchSource`` — Semantic Scholar Graph API.

Every provider normalizes results into the shared ``SearchResult`` shape so
the Retriever agent is agnostic to the underlying source (Constitution II —
contract-driven, replaceable). Add a new source by subclassing
``SearchSource`` and registering it in ``get_search_sources`` — no agent or
orchestration code changes required.

Credibility scoring (T090 / FR-024) lives here as ``score_credibility`` so
the Retriever can annotate each result consistently regardless of origin.
"""

from __future__ import annotations

import asyncio
import re
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx

from backend.core.config import settings
from backend.utils.logging import get_logger

logger = get_logger(__name__)

# Shared HTTP timeout for external search APIs (seconds).
_SEARCH_TIMEOUT = 30.0
# Naive per-source rate limiting: minimum gap between consecutive requests
# to the same provider, to stay polite with free-tier APIs.
_MIN_REQUEST_GAP: dict[str, float] = {
    "arxiv": 3.0,            # arXiv asks for ≤1 req / 3s
    "semantic_scholar": 3.0, # public endpoint is rate-limited (≈1 req / 3 s匿名)
    "web": 0.0,
}
_last_request_at: dict[str, float] = {}


@dataclass
class SearchResult:
    """A single normalized search result from any source.

    Field names align with the MongoDB ``RetrievalResult`` document and the
    ``RetrievalResultSchema`` so the Retriever can persist them directly.
    """

    title: str
    url: str
    abstract: str = ""
    excerpt: str = ""
    doi: str | None = None
    source_type: str = "web"  # web | arxiv | semantic_scholar | knowledge_base
    authors: list[str] = field(default_factory=list)
    publication_date: str | None = None
    raw_snapshot: str = ""
    credibility: str = "unknown"  # high | medium | low | unknown

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dict suitable for MongoDB persistence."""
        return {
            "title": self.title,
            "url": self.url,
            "abstract": self.abstract,
            "excerpt": self.excerpt,
            "doi": self.doi,
            "source_type": self.source_type,
            "authors": self.authors,
            "publication_date": self.publication_date,
            "raw_snapshot": self.raw_snapshot,
            "credibility": self.credibility,
        }


# ── Credibility scoring (T090 / FR-024) ────────────────────────────────

# Metadata fields used to judge completeness.
_CRITICAL_FIELDS = ("title", "url", "abstract")
_PEER_REVIEWED_TYPES = {"arxiv", "semantic_scholar"}


def score_credibility(result: SearchResult) -> str:
    """
    Classify a result's credibility per the data-model rules:

    - ``high``:   complete metadata + peer-reviewed-style source.
    - ``medium``: partial metadata missing, or a non-formal source with
                  the critical fields present.
    - ``low``:    a critical field (title/url/abstract) is missing.
    - ``unknown``: source info severely incomplete (no usable content).

    Args:
        result: The normalized search result to score.

    Returns:
        One of {"high", "medium", "low", "unknown"}.
    """
    filled = {
        "title": bool(result.title and result.title.strip()),
        "url": bool(result.url and result.url.strip()),
        "abstract": bool(result.abstract and result.abstract.strip()),
    }

    # unknown — almost nothing usable
    if not filled["title"] and not filled["abstract"]:
        return "unknown"

    # low — a critical field is missing
    if not all(filled.values()):
        return "low"

    extra_complete = bool(
        result.authors
        or result.publication_date
        or result.doi
    )

    if result.source_type in _PEER_REVIEWED_TYPES and extra_complete:
        return "high"
    if extra_complete:
        return "medium"
    return "medium" if filled["abstract"] else "low"


# ── Abstract interface ─────────────────────────────────────────────────


class SearchSource(ABC):
    """Abstract interface for a retrieval source (Constitution V).

    A source implementation MUST:
    - declare a unique ``name`` (used for logging and rate-limit keys);
    - implement ``search`` returning normalized ``SearchResult`` objects.

    Implementations are expected to handle their own transient network
    errors and return what they can — a hard failure should raise so the
    Retriever can mark the source as failed for this round.
    """

    name: str

    @abstractmethod
    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        """
        Run a search and return normalized results.

        Args:
            query: The search query string.
            limit: Maximum number of results to return.

        Returns:
            A list of ``SearchResult`` (possibly empty). Implementations
            should keep ordering by relevance as returned by the upstream
            API.
        """
        ...

    async def _respect_rate_limit(self) -> None:
        """Throttle requests to this source if a min gap is configured."""
        gap = _MIN_REQUEST_GAP.get(self.name, 0.0)
        if gap <= 0:
            return
        last = _last_request_at.get(self.name)
        if last is not None:
            elapsed = asyncio.get_event_loop().time() - last
            if elapsed < gap:
                await asyncio.sleep(gap - elapsed)
        _last_request_at[self.name] = asyncio.get_event_loop().time()


# ── Web search (Tavily) ────────────────────────────────────────────────


class WebSearchSource(SearchSource):
    """General web search via the Tavily Search API.

    Tavily returns titled pages with content snippets; we capture the
    snippet as both ``abstract`` and ``raw_snapshot`` so the citation view
    can show an excerpt even when the original URL is later unreachable
    (Constitution III — recoverability).
    """

    name = "web"
    _API_URL = "https://api.tavily.com/search"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.search_api_key

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        if not self.api_key:
            logger.warning("web_search_skipped_no_key")
            return []

        await self._respect_rate_limit()
        body = {
            "api_key": self.api_key,
            "query": query,
            "max_results": limit,
            "search_depth": "advanced",
        }
        try:
            async with httpx.AsyncClient(timeout=_SEARCH_TIMEOUT) as client:
                response = await client.post(self._API_URL, json=body)
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            logger.warning(
                "web_search_failed",
                query=query[:80],
                error=str(exc)[:200],
            )
            return []

        results: list[SearchResult] = []
        for item in data.get("results", [])[:limit]:
            title = (item.get("title") or "").strip()
            url = (item.get("url") or "").strip()
            content = (item.get("content") or "").strip()
            if not title and not url:
                continue
            result = SearchResult(
                title=title or url,
                url=url,
                abstract=content,
                excerpt=content[:500],
                source_type="web",
                raw_snapshot=content,
            )
            result.credibility = score_credibility(result)
            results.append(result)

        logger.info(
            "web_search_complete",
            query=query[:80],
            returned=len(results),
        )
        return results


# ── arXiv ──────────────────────────────────────────────────────────────


class ArxivSearchSource(SearchSource):
    """arXiv paper search via the Atom export endpoint.

    No API key required. Parses the Atom XML feed into normalized results
    with authors, abstract, published date, and DOI when present.
    """

    name = "arxiv"
    _API_URL = "https://export.arxiv.org/api/query"
    _ATOM_NS = "{http://www.w3.org/2005/Atom}"
    _ARXIV_NS = "{http://arxiv.org/schemas/atom}"

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        await self._respect_rate_limit()
        params = {
            "search_query": f"all:{query}",
            "start": 0,
            "max_results": limit,
            "sortBy": "relevance",
        }
        try:
            async with httpx.AsyncClient(timeout=_SEARCH_TIMEOUT, follow_redirects=True) as client:
                response = await client.get(self._API_URL, params=params)
                response.raise_for_status()
                xml_text = response.text
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            logger.warning(
                "arxiv_search_failed",
                query=query[:80],
                error=str(exc)[:200],
            )
            return []

        results: list[SearchResult] = []
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            logger.warning("arxiv_parse_failed", query=query[:80])
            return []

        for entry in root.findall(f"{self._ATOM_NS}entry")[:limit]:
            title_el = entry.find(f"{self._ATOM_NS}title")
            title = (title_el.text or "").strip().replace("\n", " ") if title_el is not None else ""
            id_el = entry.find(f"{self._ATOM_NS}id")
            url = (id_el.text or "").strip() if id_el is not None else ""
            summary_el = entry.find(f"{self._ATOM_NS}summary")
            abstract = (summary_el.text or "").strip() if summary_el is not None else ""
            published_el = entry.find(f"{self._ATOM_NS}published")
            published = (published_el.text or "")[:10] if published_el is not None else None
            doi_el = entry.find(f"{self._ARXIV_NS}doi")
            doi = (doi_el.text or "").strip() or None if doi_el is not None else None

            authors = [
                (name_el.text or "").strip()
                for a in entry.findall(f"{self._ATOM_NS}author")
                if (name_el := a.find(f"{self._ATOM_NS}title")) is not None
                and (name_el.text or "").strip()
            ]

            if not title and not url:
                continue

            result = SearchResult(
                title=title or url,
                url=url,
                abstract=abstract,
                excerpt=abstract[:500],
                doi=doi,
                source_type="arxiv",
                authors=authors,
                publication_date=published,
                raw_snapshot=abstract,
            )
            result.credibility = score_credibility(result)
            results.append(result)

        logger.info(
            "arxiv_search_complete",
            query=query[:80],
            returned=len(results),
        )
        return results


# ── Semantic Scholar ───────────────────────────────────────────────────


class SemanticScholarSearchSource(SearchSource):
    """Semantic Scholar Graph API paper search.

    No API key required for low-rate public usage. Returns title,
    abstract, authors, year, and externalIds (DOI/arXiv).
    """

    name = "semantic_scholar"
    _API_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
    _FIELDS = "title,abstract,authors,year,externalIds,url"

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        await self._respect_rate_limit()
        params = {
            "query": query,
            "limit": min(limit, 100),
            "fields": self._FIELDS,
        }
        try:
            async with httpx.AsyncClient(timeout=_SEARCH_TIMEOUT) as client:
                response = await client.get(self._API_URL, params=params)
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            logger.warning(
                "semantic_scholar_search_failed",
                query=query[:80],
                error=str(exc)[:200],
            )
            return []

        results: list[SearchResult] = []
        for item in data.get("data", [])[:limit]:
            title = (item.get("title") or "").strip()
            url = (item.get("url") or "").strip()
            abstract = (item.get("abstract") or "").strip()
            if not title and not url:
                continue

            year = item.get("year")
            published = str(year) if year else None

            external_ids = item.get("externalIds") or {}
            doi = (external_ids.get("DOI") or "").strip() or None
            ss_url = url or (
                f"https://www.semanticscholar.org/paper/{item.get('paperId', '')}"
            )

            authors = [
                (a.get("name") or "").strip()
                for a in (item.get("authors") or [])
                if (a.get("name") or "").strip()
            ]

            result = SearchResult(
                title=title or ss_url,
                url=ss_url,
                abstract=abstract,
                excerpt=abstract[:500],
                doi=doi,
                source_type="semantic_scholar",
                authors=authors,
                publication_date=published,
                raw_snapshot=abstract,
            )
            result.credibility = score_credibility(result)
            results.append(result)

        logger.info(
            "semantic_scholar_search_complete",
            query=query[:80],
            returned=len(results),
        )
        return results


# ── Registry / factory ─────────────────────────────────────────────────

# Source registry — maps source_type → SearchSource class.
# Add new sources here; the Retriever and orchestration resolve sources by
# these keys and never import a concrete class (Constitution V).
_SOURCE_REGISTRY: dict[str, type[SearchSource]] = {
    "web": WebSearchSource,
    "arxiv": ArxivSearchSource,
    "semantic_scholar": SemanticScholarSearchSource,
}

# Default sources used when a task config does not specify any.
DEFAULT_SOURCE_TYPES: list[str] = ["web", "arxiv", "semantic_scholar"]


def get_search_sources(source_types: list[str] | None = None) -> list[SearchSource]:
    """
    Instantiate the configured search sources.

    Args:
        source_types: Optional list of source-type keys to include.
            Defaults to ``DEFAULT_SOURCE_TYPES``.

    Returns:
        A list of ``SearchSource`` instances. Unknown keys are dropped with
        a warning so a typo never crashes the pipeline.
    """
    types = source_types or DEFAULT_SOURCE_TYPES
    sources: list[SearchSource] = []
    for key in types:
        cls = _SOURCE_REGISTRY.get(key)
        if cls is None:
            logger.warning("search_source_unknown", source_type=key)
            continue
        sources.append(cls())
    logger.info(
        "search_sources_resolved",
        requested=types,
        active=[s.name for s in sources],
    )
    return sources


def normalize_url(url: str) -> str:
    """Lowercase, strip scheme/trailing-slash for URL-based dedup."""
    u = url.strip().lower()
    u = re.sub(r"^https?://", "", u)
    u = u.rstrip("/")
    # Drop common tracking query params — keep the rest for identity.
    u = re.sub(r"[?&](utm_|ref|fbclid|gclid)[^&]*", "", u)
    u = re.sub(r"[?&=]+$", "", u)
    return u
