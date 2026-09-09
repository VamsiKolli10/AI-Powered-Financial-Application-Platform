"""Assistant endpoints: grounded, read-only Q&A over the user's own data."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.auth import current_user_id
from libs.common.errors import AppError
from libs.common.logging import get_logger
from libs.db.ids import new_id
from libs.db.session import get_session
from services.assistant import answers, guardrails, retrieval
from services.assistant.conversations import ConversationStore, InMemoryConversationStore
from services.assistant.schemas import (
    ChatRequest,
    ChatResponse,
    ConversationResponse,
    ConversationTurn,
    SourceRef,
)

log = get_logger("assistant")
router = APIRouter(prefix="/assistant", tags=["assistant"])


class ReadOnlyError(AppError):
    """400 with guidance toward the endpoint that can actually do the thing."""

    status_code = 400
    code = "READ_ONLY_ASSISTANT"


def _store(request: Request) -> ConversationStore:
    store = getattr(request.app.state, "conversations", None)
    if store is None:
        store = InMemoryConversationStore()
        request.app.state.conversations = store
    return store


@router.post("/chat", response_model=ChatResponse, summary="Ask about your finances")
async def chat(
    payload: ChatRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(current_user_id),
) -> ChatResponse:
    verdict = guardrails.check(payload.message)
    if not verdict.allowed:
        # Refused before any retrieval or model call.
        log.info("assistant_request_refused", user_id=user_id)
        raise ReadOnlyError(verdict.reason)

    conversation_id = payload.conversation_id or new_id("conv")
    store = _store(request)
    history = await store.history(conversation_id, user_id)

    context = await retrieval.retrieve(session, user_id=user_id, message=payload.message)
    draft = answers.build_answer(context)
    facts = answers.build_facts(context)

    responder = getattr(request.app.state, "responder", None)
    if responder is None:
        reply, reply_source = draft, "draft"
    else:
        reply, reply_source = await responder.respond(
            question=payload.message, facts=facts, draft=draft, history=history
        )

    now = datetime.now(UTC).isoformat()
    await store.append(
        conversation_id,
        user_id,
        [
            {"role": "user", "content": payload.message, "at": now},
            {"role": "assistant", "content": reply, "at": now},
        ],
    )

    return ChatResponse(
        conversation_id=conversation_id,
        reply=reply,
        sources=[
            SourceRef(type=s.type, category=s.category, period=s.period) for s in context.sources
        ],
        reply_source=reply_source,
    )


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationResponse,
    summary="Conversation history",
)
async def get_conversation(
    conversation_id: str,
    request: Request,
    user_id: str = Depends(current_user_id),
) -> ConversationResponse:
    # Keyed by user, so another user's conversation id simply reads as empty.
    turns = await _store(request).history(conversation_id, user_id)
    return ConversationResponse(
        conversation_id=conversation_id,
        turns=[ConversationTurn(**turn) for turn in turns],
    )
