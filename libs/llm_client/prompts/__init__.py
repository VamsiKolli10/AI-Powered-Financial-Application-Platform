"""Versioned prompt templates.

Prompts are versioned because the cache key embeds the version: bumping the prompt
must not serve answers produced by the previous one. Add a new module (v2, ...)
rather than editing an existing one in place.
"""

from libs.llm_client.prompts.categorize_v1 import (
    CATEGORIZE_PROMPT_VERSION,
    build_categorize_messages,
)

__all__ = ["CATEGORIZE_PROMPT_VERSION", "build_categorize_messages"]
