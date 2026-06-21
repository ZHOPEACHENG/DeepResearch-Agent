"""
Shared helpers for extracting / normalising fields from the research
pipeline state dict (``ResearchGraphState`` and agent ``state``).

Centralised here so agents don't each re-declare the same small extractors.
"""

from __future__ import annotations

from typing import Any


def conv_id(state: dict[str, Any]) -> str | None:
    """Extract the conversation id from agent state, or *None*."""
    cid = state.get("conversation_id")
    return str(cid) if cid else None


def coerce_citation_map(raw: Any) -> dict[str, list[str]]:
    """Normalise a ``citation_map`` from LLM JSON into ``{str: [str]}``.

    LLMs sometimes return a single id as a bare string or a mixed list.
    This coerces every value to ``list[str]`` so downstream code can
    ``.append()`` / iterate without type-guards.
    """
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[str]] = {}
    for k, v in raw.items():
        if isinstance(v, list):
            out[str(k)] = [str(x) for x in v]
        elif v is not None:
            out[str(k)] = [str(v)]
    return out
