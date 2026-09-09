"""Assistant API contracts (API_DESIGN.md)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=1000)
    conversation_id: str | None = None


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


class ConversationTurn(BaseModel):
    role: str
    content: str
    at: datetime | None = None


class ConversationResponse(BaseModel):
    conversation_id: str
    turns: list[ConversationTurn]
