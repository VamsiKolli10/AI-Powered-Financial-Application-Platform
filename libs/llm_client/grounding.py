"""Conservative validation of a rewrite's figures and their factual anchors.

The deterministic draft is the authority. A rewrite must retain its ordered numbers,
currency markers, category/merchant names and comparison direction. This deliberately
rejects some valid paraphrases rather than accepting a changed or reassigned amount.
It is a numeric guard, not a proof that arbitrary prose is semantically equivalent.
"""

from __future__ import annotations

import re
from decimal import Decimal

from libs.db.categories import CATEGORY_NAMES

_NUMBER = r"(?<![\w])[+−-]?\d+(?:,\d{3})*(?:\.\d+)?%?(?![\w])"
_CURRENCY = (
    r"CA\$|A\$|[$€£¥₹]|\b[A-Z]{3}\b|"
    r"(?i:\b(?:usd|eur|gbp|jpy|inr|cad|aud|chf|dollars?|euros?|pounds?|yen|rupees?)\b)"
)
_NUMBER_WORDS = re.compile(
    r"\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|hundred|thousand|million)\b",
    re.I,
)


def valid_rewrite(value: object, *, draft: str, facts: dict) -> bool:
    if not isinstance(value, str) or not value.strip() or len(value) > 4000:
        return False
    text = value.strip()
    # Spelled-out quantities bypass digit comparison; require the draft's wording.
    if _NUMBER_WORDS.findall(text.lower()) != _NUMBER_WORDS.findall(draft.lower()):
        return False
    names = set(CATEGORY_NAMES.values())
    for row in facts.get("largest_transactions", []):
        names.add(str(row["merchant"]))
    names.update(str(row["category"]) for row in facts.get("by_category", []))
    focus = facts.get("focus_category")
    if isinstance(focus, dict):
        names.add(str(focus["category"]))
    names.update(
        [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ]
    )
    patterns = [f"(?i:{re.escape(name)})" for name in sorted(names, key=len, reverse=True) if name]
    # Preserve relative positions of figures and labels: merely checking whether a
    # number occurs somewhere in FACTS would allow swapping two categories' totals.
    pattern = re.compile(
        "|".join(patterns + [_CURRENCY, _NUMBER, r"(?i:\b(?:more|less|included|excluded)\b)"]),
    )

    def signature(sentence: str) -> list[str]:
        result = []
        for match in pattern.finditer(sentence):
            token = match.group()
            if re.fullmatch(_NUMBER, token):
                suffix = "%" if token.endswith("%") else ""
                token = (
                    str(Decimal(token.rstrip("%").replace(",", "").replace("−", "-")).normalize())
                    + suffix
                )
            result.append(token.casefold())
        return result

    return signature(text) == signature(draft)
