"""Transaction categorization prompt, version 1."""

from libs.db.categories import CATEGORIES

CATEGORIZE_PROMPT_VERSION = "v1"

_CATEGORY_LINES = "\n".join(f"- {slug}: {description}" for slug, _, description in CATEGORIES)

SYSTEM_PROMPT = f"""You categorize bank and card transactions for a personal finance app.

Choose exactly one category slug from this list:
{_CATEGORY_LINES}

Rules:
- Reply with JSON only: {{"category": "<slug>", "confidence": <0.0-1.0>}}
- The category must be one of the slugs above, copied exactly.
- A positive amount is money coming in; it is usually income or transfers.
- If the merchant is unclear, use "other" with a low confidence rather than guessing.
- The description has already been redacted; never ask for more detail."""


def build_categorize_messages(normalized_description: str, amount: str) -> list[dict[str, str]]:
    """Build the chat messages for one categorization.

    `normalized_description` must already have been through
    `libs.common.text.normalize_description` - no raw descriptions, account numbers or
    card fragments are ever sent to the provider.
    """
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Description: {normalized_description}\nAmount: {amount}",
        },
    ]
