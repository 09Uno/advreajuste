"""Camada unificada de Vision — roteador Gemini ↔ Claude.

Seleciona provider conforme `settings.vision_provider` (default: gemini).
Fallback automático para Claude se Gemini falhar ou não estiver configurado.
"""
from __future__ import annotations

from pathlib import Path
from typing import TypeVar

from loguru import logger
from pydantic import BaseModel

from ..config import settings

T = TypeVar("T", bound=BaseModel)


def extrair_com_vision(
    pdf_path: Path,
    schema: type[T],
    prompt: str,
    provider: str | None = None,
) -> T:
    """Extrai dados estruturados de um PDF via Vision LLM.

    Ordem de tentativa:
      1. Provider configurado (default: Gemini 2.5 Flash — melhor custo)
      2. Se falhar: fallback para o outro provider

    Ambos retornam instância validada do schema Pydantic.
    """
    primary = provider or settings.vision_provider

    try:
        if primary == "gemini":
            return _extrair_gemini(pdf_path, schema, prompt)
        return _extrair_claude(pdf_path, schema, prompt)
    except Exception as e:
        logger.warning("Vision {} falhou ({}), tentando fallback", primary, e)
        fallback = "claude" if primary == "gemini" else "gemini"
        try:
            if fallback == "gemini":
                return _extrair_gemini(pdf_path, schema, prompt)
            return _extrair_claude(pdf_path, schema, prompt)
        except Exception as e2:
            raise RuntimeError(f"Vision falhou em ambos providers: {e} / {e2}") from e2


def _extrair_gemini(pdf_path: Path, schema: type[T], prompt: str) -> T:
    from .gemini_client import extrair_pdf_estruturado

    return extrair_pdf_estruturado(pdf_path, schema, prompt)  # type: ignore


def _extrair_claude(pdf_path: Path, schema: type[T], prompt: str) -> T:
    import base64
    from .client import instructor_client

    client = instructor_client()
    pdf_bytes = Path(pdf_path).read_bytes()
    b64 = base64.standard_b64encode(pdf_bytes).decode()
    return client.messages.create(
        model=settings.llm_model_vision,
        max_tokens=4096,
        max_retries=3,
        response_model=schema,
        messages=[{
            "role": "user",
            "content": [
                {"type": "document", "source": {
                    "type": "base64", "media_type": "application/pdf", "data": b64,
                }},
                {"type": "text", "text": prompt},
            ],
        }],
    )
