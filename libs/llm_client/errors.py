"""LLM failure modes. Callers treat all of these as 'fall back to the rules engine'."""


class LLMError(Exception):
    """Base class for every LLM client failure."""


class LLMUnavailable(LLMError):
    """Provider is unreachable, timing out, erroring, or the breaker is open."""


class LLMRateLimited(LLMError):
    """Our own shared token bucket refused the call before it was made."""


class LLMInvalidResponse(LLMError):
    """The model replied with something we cannot use."""
