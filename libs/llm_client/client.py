"""Thin async OpenAI wrapper: timeouts, bounded retries, JSON-only responses.

Everything above this layer deals in `LLMError`s, never provider-specific exceptions,
so swapping providers touches only this file.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Protocol, cast

from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from libs.common.logging import get_logger
from libs.llm_client.errors import LLMInvalidResponse, LLMUnavailable

if TYPE_CHECKING:  # pragma: no cover - typing only
    from openai.types.chat import ChatCompletionMessageParam

log = get_logger("llm_client")


class ChatCompleter(Protocol):
    """The provider surface used here; tests supply a fake."""

    async def complete(
        self, *, messages: list[dict[str, str]], model: str, timeout: float, max_tokens: int
    ) -> str: ...


class OpenAIChatCompleter:
    """Real provider adapter, built lazily so no key is needed unless it is used."""

    def __init__(self, api_key: str, *, base_url: str | None = None) -> None:
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def complete(
        self, *, messages: list[dict[str, str]], model: str, timeout: float, max_tokens: int
    ) -> str:
        response = await self._client.chat.completions.create(
            model=model,
            messages=cast("list[ChatCompletionMessageParam]", messages),
            temperature=0,
            max_tokens=max_tokens,
            timeout=timeout,
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content or ""


class LLMClient:
    def __init__(
        self,
        completer: ChatCompleter,
        *,
        model: str = "gpt-4o-mini",
        timeout_seconds: float = 20.0,
        max_attempts: int = 3,
        max_tokens: int = 200,
    ) -> None:
        self.completer = completer
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self.max_tokens = max_tokens

    async def complete_json(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        """One completion, retried on transport errors, parsed as JSON."""
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(self.max_attempts),
                wait=wait_exponential(multiplier=0.2, max=2),
                retry=retry_if_exception_type(Exception),
                reraise=True,
            ):
                with attempt:
                    raw = await self.completer.complete(
                        messages=messages,
                        model=self.model,
                        timeout=self.timeout_seconds,
                        max_tokens=self.max_tokens,
                    )
        except RetryError as exc:  # pragma: no cover - reraise=True makes this rare
            raise LLMUnavailable("LLM provider exhausted retries.") from exc
        except Exception as exc:
            raise LLMUnavailable(f"LLM provider call failed: {exc}") from exc

        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            raise LLMInvalidResponse(f"LLM returned non-JSON content: {raw!r}") from exc
        if not isinstance(payload, dict):
            raise LLMInvalidResponse(f"LLM returned {type(payload).__name__}, expected an object.")
        return payload
