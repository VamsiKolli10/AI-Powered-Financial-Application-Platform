"""Assistant API contracts (API_DESIGN.md)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=1000)
    conversation_id: str | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3, pattern="^[A-Za-z]{3}$")


class SourceRef(BaseModel):
    type: str
    category: str | None = None
    period: str | None = None


class ChatResponse(BaseModel):
    conversation_id: str
    reply: str
    sources: list[SourceRef]
    # "llm" or "draft" - so a caller can tell a model-worded answer from the
    # deterministic one. The figures are identical either way.
    reply_source: str
    fallback_reason: str | None = None


class AssistantStatus(BaseModel):
    mode: Literal["llm", "disabled", "unconfigured"]
    model: str | None = None
    message: str


class ConversationTurn(BaseModel):
    role: str
    content: str
    at: datetime | None = None


class ConversationResponse(BaseModel):
    conversation_id: str
    turns: list[ConversationTurn]
