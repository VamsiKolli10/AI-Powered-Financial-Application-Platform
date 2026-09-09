"""Deterministic, rule-based categorizer.

Two jobs (ARCHITECTURE.md sec. 6):
  1. Categorize before the LLM is wired in (Phase 2).
  2. Serve as the circuit-breaker fallback when the LLM is degraded (Phase 3).

It is pure, synchronous and fully testable - no I/O.
"""

from __future__ import annotations

import re
from decimal import Decimal

from libs.common.classification import Classification
from libs.db.categories import FALLBACK_CATEGORY

__all__ = ["Classification", "anomaly_score", "classify"]

# Ordered: the first matching rule wins, so put specific merchants above generic words.
_RULES: list[tuple[str, list[str]]] = [
    (
        "dining_coffee",
        [
            "starbucks",
            "dunkin",
            "peets",
            "blue bottle",
            "cafe",
            "coffee",
            "restaurant",
            "pizza",
            "burger",
            "sushi",
            "taco",
            "chipotle",
            "mcdonald",
            "subway sandwich",
            "doordash",
            "ubereats",
            "uber eats",
            "grubhub",
            "postmates",
            "bar & grill",
            "brewing",
        ],
    ),
    (
        "groceries",
        [
            "whole foods",
            "trader joe",
            "safeway",
            "kroger",
            "aldi",
            "lidl",
            "costco",
            "walmart grocery",
            "supermarket",
            "grocery",
            "food mart",
            "wegmans",
            "publix",
            "sprouts",
            "h-e-b",
            "instacart",
        ],
    ),
    (
        "fuel",
        [
            "shell",
            "chevron",
            "exxon",
            "mobil",
            "bp ",
            "texaco",
            "gas station",
            "arco",
            "electrify america",
            "chargepoint",
            "supercharger",
        ],
    ),
    (
        "transport",
        [
            "uber",
            "lyft",
            "metro",
            "transit",
            "mta",
            "bart",
            "caltrain",
            "parking",
            "toll",
            "amtrak",
            "taxi",
            "bike share",
            "scooter",
        ],
    ),
    (
        "travel",
        [
            "airlines",
            "airline",
            "delta air",
            "united air",
            "southwest air",
            "jetblue",
            "hotel",
            "marriott",
            "hilton",
            "hyatt",
            "airbnb",
            "expedia",
            "booking com",
            "hertz",
            "avis",
            "enterprise rent",
        ],
    ),
    (
        "subscriptions",
        [
            "netflix",
            "spotify",
            "hulu",
            "disney plus",
            "youtube premium",
            "prime video",
            "icloud",
            "google one",
            "dropbox",
            "notion",
            "patreon",
            "substack",
            "membership",
            "openai",
            "chatgpt",
            "github",
        ],
    ),
    (
        "entertainment",
        [
            "cinema",
            "amc ",
            "regal",
            "theatre",
            "theater",
            "steam games",
            "playstation",
            "xbox",
            "nintendo",
            "concert",
            "ticketmaster",
            "stubhub",
            "museum",
        ],
    ),
    (
        "electronics",
        [
            "apple store",
            "best buy",
            "newegg",
            "micro center",
            "b&h photo",
            "dell",
            "lenovo",
            "samsung electronics",
            "sony store",
        ],
    ),
    (
        "utilities",
        [
            "electric",
            "power company",
            "water dept",
            "water district",
            "comcast",
            "xfinity",
            "at&t",
            "verizon",
            "t-mobile",
            "spectrum",
            "utility",
            "internet",
            "waste management",
            "pg&e",
            "con edison",
        ],
    ),
    (
        "housing",
        [
            "rent",
            "landlord",
            "property mgmt",
            "property management",
            "mortgage",
            "hoa",
            "apartments",
            "realty",
        ],
    ),
    (
        "health",
        [
            "pharmacy",
            "cvs",
            "walgreens",
            "rite aid",
            "dental",
            "dentist",
            "clinic",
            "hospital",
            "medical",
            "optometry",
            "gym",
            "fitness",
            "planet fit",
            "equinox",
            "blue cross",
            "kaiser",
        ],
    ),
    (
        "education",
        [
            "university",
            "college",
            "tuition",
            "coursera",
            "udemy",
            "edx",
            "pluralsight",
            "bookstore",
            "school",
        ],
    ),
    (
        "fees",
        [
            "overdraft",
            "service fee",
            "monthly fee",
            "atm fee",
            "interest charge",
            "late fee",
            "wire fee",
            "foreign transaction fee",
            "annual fee",
        ],
    ),
    (
        "transfers",
        [
            "transfer to",
            "transfer from",
            "online transfer",
            "zelle",
            "venmo",
            "cash app",
            "wire transfer",
            "internal transfer",
        ],
    ),
    (
        "income",
        [
            "payroll",
            "direct deposit",
            "salary",
            "refund",
            "reimbursement",
            "dividend",
            "interest paid",
            "tax refund",
        ],
    ),
    (
        "shopping",
        [
            "amazon",
            "target",
            "walmart",
            "ikea",
            "home depot",
            "lowes",
            "etsy",
            "ebay",
            "nordstrom",
            "macy",
            "zara",
            "h&m",
            "uniqlo",
            "nike",
            "adidas",
            "shop",
        ],
    ),
]

_RULES_BY_SLUG: dict[str, list[str]] = dict(_RULES)
_WORD_BOUNDARY_CACHE: dict[str, re.Pattern[str]] = {}


def _matches(needle: str, haystack: str) -> bool:
    needle = needle.strip()
    # Multi-word needles are specific enough to match as substrings; single words
    # must respect boundaries so "bp" does not fire inside "bpm".
    if " " in needle:
        return needle in haystack
    pattern = _WORD_BOUNDARY_CACHE.get(needle)
    if pattern is None:
        pattern = re.compile(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])")
        _WORD_BOUNDARY_CACHE[needle] = pattern
    return bool(pattern.search(haystack))


def classify(normalized_description: str, amount: Decimal | None = None) -> Classification:
    """Classify a normalized description into a canonical category slug."""
    text = normalized_description.lower()

    # A positive amount that looks like a paycheck is income regardless of merchant text.
    # Look the keywords up by slug: an index into _RULES silently reads the wrong list
    # the moment a rule is added or reordered.
    if amount is not None and amount > 0:
        for needle in _RULES_BY_SLUG["income"]:
            if _matches(needle, text):
                return Classification("income", 0.95, "rules")

    for slug, needles in _RULES:
        for needle in needles:
            if _matches(needle, text):
                # Multi-word merchant matches are stronger signals than single keywords.
                confidence = 0.9 if " " in needle.strip() else 0.75
                return Classification(slug, confidence, "rules")

    if amount is not None and amount > 0:
        return Classification("income", 0.4, "rules")

    return Classification(FALLBACK_CATEGORY, 0.2, "rules")


def anomaly_score(amount: Decimal, *, mean_amount: Decimal, max_amount: Decimal) -> float:
    """Cheap pre-filter before any LLM review (ARCHITECTURE.md sec. 4).

    Scores an outflow against the account's own history: a charge near or above the
    historical mean scores low; a large multiple of it scores high. Inflows (deposits,
    refunds, payroll) are not fraud signals here and always score 0.
    """
    if amount >= 0:
        return 0.0
    value = abs(amount)
    if mean_amount <= 0:
        # No history yet: only extreme absolute amounts are suspicious.
        return min(1.0, float(value) / 5000.0)
    ratio = float(value / mean_amount)
    if ratio <= 2:
        score = 0.05 * ratio
    elif ratio <= 5:
        score = 0.1 + (ratio - 2) * 0.15
    else:
        score = min(0.99, 0.55 + (ratio - 5) * 0.05)
    if max_amount > 0 and value > max_amount * 2:
        score = max(score, 0.8)
    return round(min(score, 0.99), 4)
