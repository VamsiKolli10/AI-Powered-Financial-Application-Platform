"""Financial assistant prompt, version 1."""

import json

ASSISTANT_PROMPT_VERSION = "v1"

SYSTEM_PROMPT = """You are a personal finance assistant. You answer questions about the \
user's own spending.

You are given a FACTS block of figures already computed from the user's ledger, and a
DRAFT answer that is already correct.

Rules:
- Reply with JSON only: {"reply": "<1-3 sentences>"}
- Use only numbers that appear in FACTS. Never calculate a new figure, and never invent a
  merchant, category, date or total. If FACTS does not contain something, say you don't
  have it.
- The DRAFT is factually correct. Improve its wording and flow; do not change its numbers
  or add claims to it.
- You cannot move money, change records, or take any action. If asked, say so plainly.
- Be brief, warm and neutral. No financial advice, no judgement about the user's choices."""


def build_assistant_messages(
    question: str, facts: dict, draft: str, history: list[dict] | None = None
) -> list[dict[str, str]]:
    """Messages for one turn. History is trimmed by the caller before it gets here."""
    messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for turn in history or []:
        role = turn.get("role")
        if role in ("user", "assistant") and turn.get("content"):
            messages.append({"role": role, "content": str(turn["content"])})
    messages.append(
        {
            "role": "user",
            "content": (
                f"QUESTION: {question}\n\n"
                f"FACTS: {json.dumps(facts, sort_keys=True, default=str)}\n\n"
                f"DRAFT: {draft}"
            ),
        }
    )
    return messages
