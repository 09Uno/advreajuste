from __future__ import annotations

from functools import lru_cache

from ..config import settings


@lru_cache(maxsize=1)
def anthropic_client():
    import anthropic

    key = settings.anthropic_api_key
    if not key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY não configurado. Defina no .env ou variável de ambiente."
        )
    return anthropic.Anthropic(api_key=key)


@lru_cache(maxsize=1)
def instructor_client():
    import instructor

    return instructor.from_anthropic(anthropic_client())
