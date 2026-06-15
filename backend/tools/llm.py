"""
LLM Provider abstraction layer.

Defines an abstract interface for LLM operations (chat, embed)
and provides an OpenAI-compatible implementation with retry logic.

Constitution V (Extensible Architecture): new LLM providers can be added
by implementing LLMProvider without modifying any agent code.

Spec FR-016a: up to 3 exponential-backoff retries (1s/2s/4s) on LLM failure.
"""

import asyncio
from abc import ABC, abstractmethod

import httpx

from backend.core.config import settings
from backend.utils.logging import get_logger

logger = get_logger(__name__)


class LLMProvider(ABC):
    """Abstract interface for LLM operations."""

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> str:
        """
        Send a chat completion request.

        Args:
            messages: List of {"role": "...", "content": "..."} dicts.
            temperature: Sampling temperature (0.0 = deterministic).
            max_tokens: Maximum tokens in the response.

        Returns:
            The model's text response.
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
    ) -> str:
        """Send a chat completion via OpenAI-compatible API with retry."""
        url = f"{self.api_base}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.model,
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
                    model=self.model,
                    tokens_used=data.get("usage", {}).get("total_tokens"),
                )
                return content
            except (httpx.HTTPStatusError, httpx.RequestError, KeyError, IndexError) as e:
                if attempt < self._MAX_RETRIES - 1:
                    wait = self._BACKOFF_SEQUENCE[attempt]
                    logger.warning(
                        "llm_chat_retry",
                        model=self.model,
                        attempt=attempt + 1,
                        wait=wait,
                        error=str(e)[:200],
                    )
                    await asyncio.sleep(wait)
                    continue
                logger.error(
                    "llm_chat_failed",
                    model=self.model,
                    url=url,
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


# ── Singleton Provider ───────────────────────────────────────────────

_provider: LLMProvider | None = None


def get_llm_provider() -> LLMProvider:
    """Get or create the global LLM provider instance."""
    global _provider
    if _provider is None:
        _provider = OpenAICompatibleProvider()
        logger.info(
            "llm_provider_initialized",
            model=_provider.model,
            embed_model=_provider.embed_model,
            api_base=_provider.api_base,
        )
    return _provider


def set_llm_provider(provider: LLMProvider) -> None:
    """Override the global LLM provider (useful for testing)."""
    global _provider
    _provider = provider
