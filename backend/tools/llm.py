"""
LangChain LLM factory and utilities.

Provides per-agent model instances configured from application
settings, plus the ``safe_json_loads`` helper for best-effort JSON
extraction from LLM outputs that bypass structured mode.

Uses ``langchain.chat_models.init_chat_model`` for automatic provider
routing — models like ``"gpt-4o"`` and ``"claude-sonnet-4-6"`` are
resolved to the correct provider class without manual wiring.

Usage::

    from backend.tools.llm import get_chat_model

    model = get_chat_model("planner", temperature=0.3, max_tokens=2048)
    response = await model.ainvoke(messages)

    # Structured output (preferred — guarantees valid JSON):
    structured = model.with_structured_output(PlanOutput, method="json_schema")
    plan: PlanOutput = await structured.ainvoke(messages)

    # Fallback JSON extraction:
    from backend.tools.llm import safe_json_loads
    data = safe_json_loads(raw_text)
"""

from __future__ import annotations

import json
from contextvars import ContextVar
from functools import lru_cache

from langchain.chat_models import init_chat_model
from langchain.embeddings import init_embeddings

from backend.core.config import settings

# Per-request override for deep thinking — set by chat_service at the start
# of each request from SendMessageRequest.deep_thinking, then read by
# get_chat_model().  Falls back to settings.llm_deep_thinking when unset.
_deep_thinking_override: ContextVar[bool | None] = ContextVar(
    "deep_thinking_override", default=None,
)


def _resolve_model(name: str) -> str:
    """Map a logical agent name to a concrete model string.

    Per-agent overrides (``planner_model``, etc.) take precedence;
    missing overrides fall back to ``settings.llm_model``.
    """
    overrides: dict[str, str | None] = {
        "summarization": settings.summarization_model,
        "planner": settings.planner_model,
        "analyzer": settings.analyzer_model,
        "writer": settings.writer_model,
    }
    if name in overrides:
        return overrides[name] or settings.llm_model
    return settings.llm_model


def get_chat_model(
    name: str = "default",
    *,
    temperature: float = 0.3,
    max_tokens: int = 4096,
    model_override: str | None = None,
) -> "BaseChatModel":  # type: ignore[no-any-unimported]
    """Build a (cached) LangChain chat model instance for a logical agent name.

    Uses ``init_chat_model`` for automatic provider routing.

    When ``settings.llm_deep_thinking`` is ``False`` (default), reasoning is
    explicitly disabled so that ``with_structured_output()`` can use
    ``tool_choice`` — thinking mode rejects it at the API level.

    When ``True``, thinking stays on for compatible models; agents that need
    structured output must fall back to prompt-based extraction.

    Args:
        name: One of ``"default"``, ``"summarization"``, ``"planner"``,
            ``"analyzer"``, ``"writer"``.
        temperature: Sampling temperature.
        max_tokens: Maximum output tokens for this instance.
    Returns:
        A LangChain ``BaseChatModel`` instance.
    """
    model = model_override or _resolve_model(name)
    chat_model = init_chat_model(
        model,
        temperature=temperature,
        max_tokens=max_tokens,
        max_retries=3,
        timeout=120.0,
        request_timeout=120.0,
    )

    # ── Deep thinking detection ────────────────────────────────────
    # Provider-agnostic: any model class with a ``reasoning`` field
    # potentially has a thinking mode that rejects tool_choice.
    # Per-request ContextVar takes precedence over global settings.
    deep_thinking = _deep_thinking_override.get()
    if deep_thinking is None:
        deep_thinking = settings.llm_deep_thinking

    has_reasoning = hasattr(chat_model, "reasoning")

    if has_reasoning and not deep_thinking:
        # User didn't ask for thinking → keep it disabled so
        # with_structured_output() can use tool_choice safely.
        chat_model.extra_body = {"thinking": {"type": "disabled"}}

    if not has_reasoning and deep_thinking:
        # User asked for thinking but this model doesn't support it.
        # Use a plain logging call to avoid circular import with utils.logging.
        import logging
        _log = logging.getLogger(__name__)
        _log.warning(
            "LLM_DEEP_THINKING=True but model '%s' does not support deep thinking — ignored",
            model,
        )

    return chat_model


def set_deep_thinking(enabled: bool) -> None:
    """Set the per-request deep thinking override (called by chat_service).

    This takes precedence over ``settings.llm_deep_thinking`` for the
    current asyncio task and all sub-tasks (ContextVar is inherited).
    """
    _deep_thinking_override.set(enabled)


@lru_cache(maxsize=1)
def get_embedding_model() -> "Embeddings":  # type: ignore[no-any-unimported]
    """Build a cached embedding model instance via ``init_embeddings``."""
    return init_embeddings(
        settings.llm_embed_model, provider="openai",
        max_retries=3,
        timeout=60.0,
    )


# ── JSON utility ───────────────────────────────────────────────────────


def safe_json_loads(text: str) -> dict | None:
    """Best-effort JSON extraction from a string that may contain extra prose.

    LLMs sometimes wrap JSON in ```json ... ``` fences or append trailing
    sentences.  This strips fences, then trims to the outermost ``{ ... }``
    object so a stray trailing sentence does not break parsing.

    For new code prefer ``model.with_structured_output(PydanticModel)`` —
    it guarantees valid JSON at the API level.

    Returns ``None`` when no JSON object can be recovered.
    """
    if not text:
        return None
    s = text.strip()
    # Strip ```json ... ``` fences if present.
    if s.startswith("```"):
        s = s.split("\n", 1)[-1] if "\n" in s else s
        s = s.removesuffix("```").strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    # Fall back to slicing between the first { and the last }.
    start, end = s.find("{"), s.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(s[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None
