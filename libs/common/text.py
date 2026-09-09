"""Description normalization and redaction.

Nothing reaches the LLM provider that has not been through `normalize_description`
(ARCHITECTURE.md sec. 7): card fragments, long digit runs and store numbers are
stripped, and the result doubles as the cache key for categorization.
"""

import hashlib
import re

_CARD_TAIL = re.compile(r"\b(?:x{2,}|\*{2,})\s*\d{2,6}\b", re.IGNORECASE)
_LONG_DIGITS = re.compile(r"\b\d{4,}\b")
_STORE_NUMBER = re.compile(r"#\s*\d+")
_DATE_LIKE = re.compile(r"\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b")
_PAYMENT_NOISE = re.compile(
    r"\b(pos|purchase|debit card|credit card|card|payment|pmt|recurring|ach|"
    r"visa|mastercard|amex|tst\*|sq \*|sq\*|paypal \*|pp\*|ref)\b",
    re.IGNORECASE,
)
# Bank descriptors commonly end "<MERCHANT> <CITY> <ST>"; drop the city and state so
# every branch of a merchant normalizes (and therefore caches) identically.
_CITY_STATE_TAIL = re.compile(r"\s+[A-Za-z]+\s+[A-Z]{2}\s*$")
_CITY2_STATE_TAIL = re.compile(r"\s+[A-Za-z]+\s+[A-Za-z]+\s+[A-Z]{2}\s*$")
_US_STATE_TAIL = re.compile(r"\s+[A-Z]{2}\s*$")
# Order/reference codes: tokens mixing letters and digits, e.g. "2H4KD9", "T-4412".
_REF_CODE = re.compile(r"\b(?=\w*[0-9])(?=\w*[a-z])[a-z0-9]{4,}\b", re.IGNORECASE)
_WHITESPACE = re.compile(r"\s+")


def normalize_description(raw: str) -> str:
    """Lowercase, redacted, whitespace-collapsed merchant string."""
    text = raw.strip()
    text = _CARD_TAIL.sub(" ", text)
    text = _STORE_NUMBER.sub(" ", text)
    text = _DATE_LIKE.sub(" ", text)
    # Strip a two-word city ("SAN JOSE CA") first, then a one-word city, always
    # leaving at least one token of merchant name behind.
    if len(text.split()) >= 4 and _CITY2_STATE_TAIL.search(text):
        text = _CITY2_STATE_TAIL.sub(" ", text)
    elif len(text.split()) >= 3:
        text = _CITY_STATE_TAIL.sub(" ", text)
    text = _US_STATE_TAIL.sub(" ", text)
    text = _LONG_DIGITS.sub(" ", text)
    text = _REF_CODE.sub(" ", text)
    text = _PAYMENT_NOISE.sub(" ", text)
    text = re.sub(r"[^\w\s&'-]", " ", text)
    text = _WHITESPACE.sub(" ", text).strip().lower()
    return text or raw.strip().lower()


def guess_merchant(normalized: str) -> str | None:
    """Best-effort human-facing merchant name from a normalized description."""
    if not normalized:
        return None
    words = [w for w in normalized.split() if len(w) > 1][:3]
    return " ".join(w.capitalize() for w in words) or None


def merchant_key(normalized: str, *, tokens: int = 2) -> str:
    """Location-independent merchant key.

    "starbucks seattle" and "starbucks portland" collapse to "starbucks", so every
    branch of the same merchant shares one cached classification.
    """
    return " ".join(normalized.split()[:tokens]) or normalized


def cache_key(normalized: str, *, namespace: str = "cat", version: str = "v1") -> str:
    """Redis key for a cached classification, keyed on the merchant, not the branch."""
    digest = hashlib.sha256(merchant_key(normalized).encode("utf-8")).hexdigest()[:32]
    return f"llm:{namespace}:{version}:{digest}"
