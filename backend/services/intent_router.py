"""
IntentRouter — LLM-based message intent classification.

Classifies every user message as:
- chat:       General conversation — reply with LLM using conversation context
- research:   User wants deep research on a topic — trigger pipeline

Uses a fast/cheap model with temperature=0 for deterministic classification.
Falls back to "chat" on any classification failure.
"""

from backend.tools.llm import get_llm_provider
from backend.utils.logging import get_logger

logger = get_logger(__name__)

INTENT_CLASSIFICATION_PROMPT = """Classify the user's intent into exactly one of these categories:

- chat: General conversation, greeting, follow-up question, or clarification.
  Most messages are chat, including questions about previous discussion.
- research: The user wants a deep, multi-source research investigation.
  Keywords: "research", "investigate", "deep dive", "literature review",
  "find papers on", "what is the current state of", etc.

Reply with ONLY one word: chat or research."""


async def classify_intent(user_message: str) -> str:
    """
    Classify a user message's intent.

    Returns one of: "chat", "research".
    Falls back to "chat" on any error.
    """
    try:
        provider = get_llm_provider()
        messages = [
            {"role": "system", "content": INTENT_CLASSIFICATION_PROMPT},
            {"role": "user", "content": user_message[:2000]},
        ]
        result = await provider.chat(
            messages=messages,
            temperature=0.0,
            max_tokens=10,
        )
        intent = result.strip().lower()
        if intent in ("chat", "research"):
            logger.info("intent_classified", intent=intent, preview=user_message[:60])
            return intent

        logger.warning(
            "intent_unrecognized",
            raw=result[:30],
            falling_back_to="chat",
        )
        return "chat"

    except Exception:
        logger.error("intent_classification_failed", exc_info=True)
        return "chat"
