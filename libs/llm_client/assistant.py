"""LLM rewriting of a grounded draft answer.

The model never computes anything: it receives figures already derived from the ledger
plus a correct draft, and returns better wording. If it fails, is rate limited, or comes
back invalid, the computed draft is what the user gets. Numerical validation is
conservative; it is not a guarantee of semantic equivalence for arbitrary prose.
"""

from __future__ import annotations

from libs.common.logging import get_logger
from libs.llm_client.client import LLMClient
from libs.llm_client.errors import LLMError
from libs.llm_client.grounding import valid_rewrite
from libs.llm_client.metrics import LLM_CALLS
from libs.llm_client.prompts.assistant_v1 import build_assistant_messages
from libs.llm_client.rate_limit import InMemoryRateLimiter, TokenBucketRateLimiter

log = get_logger("llm_client.assistant")

MAX_HISTORY_TURNS = 6


class AssistantResponder:
    def __init__(
        self,
        client: LLMClient,
        *,
        rate_limiter: TokenBucketRateLimiter | InMemoryRateLimiter,
    ) -> None:
        self.client = client
        self.rate_limiter = rate_limiter

    async def respond(
        self, *, question: str, facts: dict, draft: str, history: list[dict] | None = None
    ) -> tuple[str, str, str | None]:
        """Return reply, source, and the reason a model reply could not be used."""
        allowed, _retry_after = await self.rate_limiter.acquire()
        if not allowed:
            LLM_CALLS.labels("rate_limited").inc()
            return draft, "draft", "rate_limited"

        messages = build_assistant_messages(
            question, facts, draft, (history or [])[-MAX_HISTORY_TURNS:]
        )
        try:
            payload = await self.client.complete_json(messages)
        except LLMError as exc:
            LLM_CALLS.labels("error").inc()
            log.warning("assistant_llm_failed_using_draft", error_type=type(exc).__name__)
            return draft, "draft", "provider_unavailable"

        value = payload.get("reply")
        reply = value.strip() if isinstance(value, str) else ""
        if not valid_rewrite(reply, draft=draft, facts=facts):
            LLM_CALLS.labels("invalid").inc()
            return draft, "draft", "invalid_response"

        LLM_CALLS.labels("success").inc()
        return reply, "llm", None
