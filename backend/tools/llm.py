"""
LLM Provider abstraction layer.

Defines an abstract interface for LLM operations (chat, embed)
and provides an OpenAI-compatible implementation with retry logic.

Constitution V (Extensible Architecture): new LLM providers can be added
by implementing LLMProvider without modifying any agent code.

Spec FR-016a: up to 3 exponential-backoff retries (1s/2s/4s) on LLM failure.
"""

import asyncio
import json
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx

from backend.core.config import settings
from backend.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class LLMResponse:
    """Rich return from ``chat_with_tools()`` when tool definitions are provided.

    ``content`` is the model's plain-text response (may be empty if the model
    only returns tool calls). ``tool_calls`` is a list of parsed tool calls,
    each with ``id``, ``name``, and ``arguments``.
    """

    content: str = ""
    tool_calls: list[dict] = field(default_factory=list)


class LLMProvider(ABC):
    """Abstract interface for LLM operations."""

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 4096,
        model: str | None = None,
    ) -> str:
        """
        Send a chat completion request.

        Args:
            messages: List of {"role": "...", "content": "..."} dicts.
            temperature: Sampling temperature (0.0 = deterministic).
            max_tokens: Maximum tokens in the response.
            model: Override the default model for this call.

        Returns:
            The model's text response.
        """
        ...

    @abstractmethod
    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        model: str | None = None,
    ) -> AsyncIterator[str]:
        """
        Stream a chat completion response token-by-token via SSE.

        Yields content tokens as they arrive from the API.
        """
        yield ""  # pragma: no cover — abstract, never executed

    @abstractmethod
    async def chat_with_tools(
        self,
        messages: list[dict[str, str]],
        tools: list[dict] | None = None,
        tool_choice: str = "auto",
        temperature: float = 0.3,
        max_tokens: int = 4096,
        model: str | None = None,
    ) -> LLMResponse:
        """Send a chat completion with optional function-calling tools.

        Returns ``LLMResponse`` with ``content`` and any parsed ``tool_calls``.
        """
        ...

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """
        Generate embeddings for the given texts.

        Args:
            texts: List of text strings to embed.

        Returns:
            List of embedding vectors (each is a list of floats).
        """
        ...

    @abstractmethod
    async def summarize_page(
        self,
        content: str,
        max_summary_tokens: int = 500,
        model: str | None = None,
    ) -> str:
        """Summarize long webpage content into a concise abstract.

        Uses a smaller/cheaper model by default (``summarization_model``).
        """
        ...


_SUMMARIZE_PAGE_SYSTEM = (
    "你是一个精确的文本摘要器。用 3-5 句简洁的中文总结以下网页内容。"
    "只提取关键事实、发现和主张。输出语言与原文一致。不要添加观点。"
)


class OpenAICompatibleProvider(LLMProvider):
    """
    OpenAI-compatible chat/embedding provider.

    Works with OpenAI, Azure OpenAI, and any OpenAI-compatible API
    (vLLM, Ollama, LiteLLM, etc.) by configuring LLM_API_BASE.

    Retries up to 3 times with exponential backoff (1s → 2s → 4s) per
    spec FR-016a; all retries exhausted → raise last error to caller.
    """

    _MAX_RETRIES = 4          # 1 initial + 3 retries
    _BACKOFF_SEQUENCE = (1, 2, 4)

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        model: str | None = None,
        embed_model: str | None = None,
    ):
        self.api_key = api_key or settings.llm_api_key
        self.api_base = (api_base or settings.llm_api_base).rstrip("/")
        self.model = model or settings.llm_model
        self.embed_model = embed_model or settings.llm_embed_model

    # ── Chat ─────────────────────────────────────────────────────────

    async def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 4096,
        model: str | None = None,
    ) -> str:
        """Send a chat completion via OpenAI-compatible API with retry."""
        url = f"{self.api_base}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        effective_model = model or self.model
        body = {
            "model": effective_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        for attempt in range(self._MAX_RETRIES):
            try:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    response = await client.post(url, headers=headers, json=body)
                    response.raise_for_status()
                    data = response.json()
                content = data["choices"][0]["message"]["content"]
                logger.info(
                    "llm_chat_complete",
                    model=effective_model,
                    tokens_used=data.get("usage", {}).get("total_tokens"),
                )
                return content
            except (httpx.HTTPStatusError, httpx.RequestError, KeyError, IndexError) as e:
                if attempt < self._MAX_RETRIES - 1:
                    wait = self._BACKOFF_SEQUENCE[attempt]
                    logger.warning(
                        "llm_chat_retry",
                        model=effective_model,
                        attempt=attempt + 1,
                        wait=wait,
                        error=str(e)[:200],
                    )
                    await asyncio.sleep(wait)
                    continue
                logger.error(
                    "llm_chat_failed",
                    model=effective_model,
                    url=url,
                    attempts=self._MAX_RETRIES,
                    error=str(e)[:500],
                )
                raise

    # ── Chat Stream ────────────────────────────────────────────────────

    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        model: str | None = None,
    ) -> AsyncIterator[str]:
        """Stream chat tokens via OpenAI-compatible SSE endpoint."""
        url = f"{self.api_base}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        effective_model = model or self.model
        body = {
            "model": effective_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }

        for attempt in range(self._MAX_RETRIES):
            try:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    async with client.stream("POST", url, headers=headers, json=body) as response:
                        response.raise_for_status()
                        async for line in response.aiter_lines():
                            if not line.startswith("data: "):
                                continue
                            data_str = line[6:]
                            if data_str == "[DONE]":
                                break
                            try:
                                data = json.loads(data_str)
                                delta = data["choices"][0].get("delta", {})
                                token = delta.get("content", "")
                                if token:
                                    yield token
                            except (json.JSONDecodeError, KeyError, IndexError):
                                continue
                logger.info(
                    "llm_chat_stream_complete",
                    model=effective_model,
                )
                return
            except (httpx.HTTPStatusError, httpx.RequestError) as e:
                if attempt < self._MAX_RETRIES - 1:
                    wait = self._BACKOFF_SEQUENCE[attempt]
                    logger.warning(
                        "llm_chat_stream_retry",
                        model=effective_model,
                        attempt=attempt + 1,
                        wait=wait,
                        error=str(e)[:200],
                    )
                    await asyncio.sleep(wait)
                    continue
                logger.error(
                    "llm_chat_stream_failed",
                    model=effective_model,
                    attempts=self._MAX_RETRIES,
                    error=str(e)[:500],
                )
                raise

    # ── Chat with Tools ───────────────────────────────────────────────

    async def chat_with_tools(
        self,
        messages: list[dict[str, str]],
        tools: list[dict] | None = None,
        tool_choice: str = "auto",
        temperature: float = 0.3,
        max_tokens: int = 4096,
        model: str | None = None,
    ) -> LLMResponse:
        """Send a chat completion with optional function-calling tools via the
        OpenAI-compatible API. Returns a rich ``LLMResponse`` with parsed tool calls.

        Retries are identical to ``chat()``: 4 total attempts, exponential backoff.
        """
        url = f"{self.api_base}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        effective_model = model or self.model
        body: dict[str, Any] = {
            "model": effective_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = tool_choice

        for attempt in range(self._MAX_RETRIES):
            try:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    response = await client.post(url, headers=headers, json=body)
                    response.raise_for_status()
                    data = response.json()
                msg = data["choices"][0]["message"]
                content = msg.get("content") or ""
                raw_tool_calls = msg.get("tool_calls") or []
                tool_calls: list[dict] = []
                for tc in raw_tool_calls:
                    func = tc.get("function", {})
                    args_str = func.get("arguments", "{}")
                    try:
                        arguments = json.loads(args_str)
                    except (json.JSONDecodeError, TypeError):
                        arguments = {}
                    tool_calls.append({
                        "id": tc.get("id", ""),
                        "name": func.get("name", ""),
                        "arguments": arguments,
                    })
                logger.info(
                    "llm_chat_tools_complete",
                    model=effective_model,
                    tokens_used=data.get("usage", {}).get("total_tokens"),
                    tool_calls=len(tool_calls),
                )
                return LLMResponse(content=content, tool_calls=tool_calls)
            except (httpx.HTTPStatusError, httpx.RequestError, KeyError, IndexError) as e:
                if attempt < self._MAX_RETRIES - 1:
                    wait = self._BACKOFF_SEQUENCE[attempt]
                    logger.warning(
                        "llm_chat_tools_retry",
                        model=effective_model,
                        attempt=attempt + 1,
                        wait=wait,
                        error=str(e)[:200],
                    )
                    await asyncio.sleep(wait)
                    continue
                logger.error(
                    "llm_chat_tools_failed",
                    model=effective_model,
                    attempts=self._MAX_RETRIES,
                    error=str(e)[:500],
                )
                raise

    # ── Embed ────────────────────────────────────────────────────────

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings via OpenAI-compatible API with retry."""
        url = f"{self.api_base}/embeddings"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.embed_model,
            "input": texts,
        }

        for attempt in range(self._MAX_RETRIES):
            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    response = await client.post(url, headers=headers, json=body)
                    response.raise_for_status()
                    data = response.json()
                embeddings = [item["embedding"] for item in data["data"]]
                logger.info("llm_embed_complete", count=len(embeddings))
                return embeddings
            except (httpx.HTTPStatusError, httpx.RequestError, KeyError, IndexError) as e:
                if attempt < self._MAX_RETRIES - 1:
                    wait = self._BACKOFF_SEQUENCE[attempt]
                    logger.warning(
                        "llm_embed_retry",
                        model=self.embed_model,
                        attempt=attempt + 1,
                        wait=wait,
                        error=str(e)[:200],
                    )
                    await asyncio.sleep(wait)
                    continue
                logger.error(
                    "llm_embed_failed",
                    model=self.embed_model,
                    url=url,
                    attempts=self._MAX_RETRIES,
                    error=str(e)[:500],
                )
                raise

    # ── Summarize page ──────────────────────────────────────────────────

    async def summarize_page(
        self,
        content: str,
        max_summary_tokens: int = 500,
        model: str | None = None,
    ) -> str:
        """Summarize webpage content with the summarization model.

        The content is truncated to ``settings.max_content_length`` before
        being sent to the LLM to avoid blowing through the context window.
        """
        effective_model = model or settings.summarization_model
        cap = settings.max_content_length
        truncated = content[:cap] if len(content) > cap else content
        messages = [
            {"role": "system", "content": _SUMMARIZE_PAGE_SYSTEM},
            {"role": "user", "content": truncated},
        ]
        return await self.chat(
            messages=messages,
            temperature=0.1,
            max_tokens=max_summary_tokens,
            model=effective_model,
        )


# ── Named Provider Registry ─────────────────────────────────────────

_providers: dict[str, LLMProvider] = {}

# Map logical names to model config keys.
_MODEL_MAP: dict[str, str | None] = {
    "default": None,             # None means "use self.model" → settings.llm_model
    "summarization": None,       # Will resolve to settings.summarization_model at init
    "planner": None,
    "analyzer": None,
    "writer": None,
}


def _model_for_name(name: str) -> str | None:
    """Resolve a logical provider name to a model name.

    - ``"default"`` → ``settings.llm_model``
    - ``"summarization"`` → ``settings.summarization_model``
    - ``"planner"`` → ``settings.planner_model or settings.llm_model``
    - ``"analyzer"`` → ``settings.analyzer_model or settings.llm_model``
    - ``"writer"`` → ``settings.writer_model or settings.llm_model``
    - unknown name → ``settings.llm_model`` (safe fallback)
    """
    overrides: dict[str, str | None] = {
        "summarization": settings.summarization_model,
        "planner": settings.planner_model,
        "analyzer": settings.analyzer_model,
        "writer": settings.writer_model,
    }
    if name in overrides:
        return overrides[name] or settings.llm_model
    if name == "default":
        return settings.llm_model
    return settings.llm_model


def get_llm_provider(name: str = "default") -> LLMProvider:
    """Get or create a named LLM provider instance.

    Each logical name maps to a distinct model configuration so agents
    can use appropriately-sized models without threading a ``model=``
    parameter through every call site::

        get_llm_provider("summarization")  # cheap model for page summaries
        get_llm_provider("analyzer")       # powerful model for ReAct analysis

    The plain ``get_llm_provider()`` call (no argument) returns the
    ``"default"`` provider (``settings.llm_model``), preserving backward
    compatibility across the entire codebase.
    """
    global _providers
    if name not in _providers:
        model = _model_for_name(name)
        _providers[name] = OpenAICompatibleProvider(model=model)
        logger.info(
            "llm_provider_initialized",
            name=name,
            model=model,
            embed_model=_providers[name].embed_model,
            api_base=_providers[name].api_base,
        )
    return _providers[name]


def set_llm_provider(provider: LLMProvider, name: str = "default") -> None:
    """Override a named LLM provider (useful for testing)."""
    global _providers
    _providers[name] = provider


# ── LLM output parsing ───────────────────────────────────────────────


def safe_json_loads(text: str) -> dict | None:
    """Best-effort JSON extraction from an LLM response.

    LLMs often wrap JSON in ```json ... ``` fences or append stray prose.
    This strips fences, then trims to the outermost ``{ ... }`` object so a
    trailing sentence does not break parsing. Returns ``None`` when no JSON
    object can be recovered — callers should treat that as a parse failure
    and apply their fallback (e.g. re-prompt or a safe default structure).
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
