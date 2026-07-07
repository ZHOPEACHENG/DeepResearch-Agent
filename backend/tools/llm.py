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
from functools import lru_cache

from langchain.chat_models import init_chat_model
from langchain.embeddings import init_embeddings

from backend.core.config import settings
from langchain_deepseek import ChatDeepSeek

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

    # DeepSeek 推理模型默认开启思考模式，思考模式拒绝 tool_choice。
    # function_calling 依靠 tool_choice，所以必须关闭思考。
    if isinstance(chat_model, ChatDeepSeek):
        chat_model.extra_body = {"thinking": {"type": "disabled"}}

        # DeepSeek API 对 dict 形式的 tool_choice
        # （如 {"type":"function","function":{"name":"AnalyzerOutput"}}）支持不稳定，
        # 有时会忽略并返回纯文本 → with_structured_output 返回 None。
        # "required" 字符串形式更可靠，且只绑定一个工具时效果完全相同。
        _orig_get_request_payload = chat_model._get_request_payload

        def _patched_get_request_payload(
            input_, *, stop=None, **kwargs,
        ) -> dict:
            payload = _orig_get_request_payload(input_, stop=stop, **kwargs)
            tc = payload.get("tool_choice")
            if isinstance(tc, dict):
                payload["tool_choice"] = "required"
            return payload

        chat_model._get_request_payload = _patched_get_request_payload  # type: ignore[method-assign]

    return chat_model


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

    When output is truncated (model hit max_tokens), missing closing brackets
    are repaired before parsing.

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
    start = s.find("{")
    if start == -1:
        return None
    end = s.rfind("}")
    if end != -1 and end > start:
        try:
            return json.loads(s[start : end + 1])
        except json.JSONDecodeError:
            pass
    # Final fallback: repair truncated JSON by closing unmatched brackets.
    # Use a stack so closes happen in correct reverse order: inner objects
    # first, then arrays, then outer objects.
    candidate = s[start:]
    bracket_stack: list[str] = []
    in_string = False
    escape = False
    for ch in candidate:
        if escape:
            escape = False
            continue
        if ch == '\\':
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            bracket_stack.append('}')
        elif ch == '}':
            if bracket_stack and bracket_stack[-1] == '}':
                bracket_stack.pop()
        elif ch == '[':
            bracket_stack.append(']')
        elif ch == ']':
            if bracket_stack and bracket_stack[-1] == ']':
                bracket_stack.pop()
    if bracket_stack:
        candidate += ''.join(reversed(bracket_stack))
    if candidate != s[start:]:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            return None
    return None
