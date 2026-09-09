"""Prefixed, sortable public identifiers (tx_..., acct_..., usr_...)."""

import secrets
import time

_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"


def _b36(n: int) -> str:
    if n == 0:
        return "0"
    out = []
    while n:
        n, rem = divmod(n, 36)
        out.append(_ALPHABET[rem])
    return "".join(reversed(out))


def new_id(prefix: str) -> str:
    """Time-prefixed random id: roughly sortable, collision-safe enough for this scale."""
    return f"{prefix}_{_b36(int(time.time() * 1000))}{secrets.token_hex(4)}"
