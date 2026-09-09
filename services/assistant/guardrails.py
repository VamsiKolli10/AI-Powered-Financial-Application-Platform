"""Read-only guardrail.

The assistant can read and explain financial data; it can never cause a write. That is
enforced here, before any model call, rather than by asking the model to behave: a
prompt is guidance, this is a gate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# (pattern, what the user should use instead)
_WRITE_INTENTS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"\b(transfer|move|send|wire|withdraw|deposit)\b.{0,30}"
            r"(\$|\d|money|funds|savings|checking|account)",
            re.IGNORECASE,
        ),
        "This assistant cannot move money. Money movement is out of scope for this platform.",
    ),
    (
        re.compile(r"\b(pay|paying)\b.{0,20}\b(bill|card|invoice|balance|someone|rent)\b", re.I),
        "This assistant cannot make payments. Money movement is out of scope for this platform.",
    ),
    (
        re.compile(
            r"\b(delete|remove|erase|undo|reverse|void)\b.{0,30}"
            r"\b(transaction|payment|charge|record|account|history)\b",
            re.I,
        ),
        "This assistant cannot delete records. Transactions are immutable by design; "
        "correct a category with PATCH /api/v1/transactions/{id} instead.",
    ),
    (
        re.compile(
            r"\b(change|update|edit|set|recategori[sz]e|fix)\b.{0,30}"
            r"\b(category|categori[sz]ation|merchant|amount|transaction)\b",
            re.I,
        ),
        "This assistant is read-only. Correct a category with " "PATCH /api/v1/transactions/{id}.",
    ),
    (
        re.compile(
            r"\b(create|add|open|close|cancel)\b.{0,20}\b(account|transaction|budget)\b", re.I
        ),
        "This assistant is read-only. Use the Transactions API to add records.",
    ),
    (
        re.compile(r"\b(mark|set)\b.{0,40}\b(read|unread)\b", re.I),
        "This assistant is read-only. Use PATCH /api/v1/notifications/{id} instead.",
    ),
]

# Questions that merely mention a write-ish word are still questions.
_QUESTION_PREFIX = re.compile(
    r"^\s*(how much|how many|what|when|where|which|why|who|did|do|does|can you (tell|show|explain)"
    r"|show me|tell me|list|summari[sz]e|compare|explain)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class GuardrailVerdict:
    allowed: bool
    reason: str = ""


def check(message: str) -> GuardrailVerdict:
    """Decide whether a message is a question we may answer."""
    text = message.strip()
    if not text:
        return GuardrailVerdict(False, "Ask a question about your spending.")

    for pattern, guidance in _WRITE_INTENTS:
        if pattern.search(text):
            # "How much did I transfer to savings?" is a question about history, not an
            # instruction to move money.
            if _QUESTION_PREFIX.match(text) and not _is_imperative_write(text):
                continue
            return GuardrailVerdict(False, guidance)
    return GuardrailVerdict(True)


_IMPERATIVE_WRITE = re.compile(
    r"\b(please\s+)?(transfer|move|send|pay|delete|remove|update|change|set|create|add|close|cancel)\b"
    r"(?!\s+(did|do|does|was|were|have|has|is|are))",
    re.IGNORECASE,
)


def _is_imperative_write(text: str) -> bool:
    """True when the sentence tells us to do something, rather than asking about the past."""
    # "Can you move $50 to savings" is phrased as a question but is still an instruction.
    if re.match(r"^\s*(can|could|would|will)\s+you\b", text, re.IGNORECASE):
        return bool(_IMPERATIVE_WRITE.search(text))
    return False
