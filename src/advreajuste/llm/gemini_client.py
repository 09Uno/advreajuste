"""Cliente Gemini 2.5 Flash — vision primário por custo-benefício.

Preço (abril/2026): $0.15 input / $0.60 output por MTok = ~7× mais barato
que Claude Haiku 4.5 e ~20× mais barato que Claude Sonnet 4.6.

Suporta PDF nativamente (até 3.600 páginas em um único request), PT-BR,
structured output via Pydantic schema.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel

from ..config import settings


@lru_cache(maxsize=1)
def gemini_client():
    from google import genai  # type: ignore

    # Tenta ler de várias fontes (em ordem):
    # 1. Variável de ambiente ADVREAJUSTE_GEMINI_API_KEY
    # 2. st.secrets["gemini_api_key"] (Streamlit Cloud)
    api_key = settings.gemini_api_key
    if not api_key:
        try:
            import streamlit as st  # noqa
            api_key = st.secrets.get("gemini_api_key")
        except Exception:
            pass

    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY não configurado. "
            "Obtenha chave gratuita em https://aistudio.google.com/app/apikey "
            "e adicione `gemini_api_key = \"AIza...\"` nas Secrets do Streamlit Cloud."
        )
    return genai.Client(api_key=api_key)


def extrair_pdf_estruturado(
    pdf_path: Path,
    schema: type[BaseModel],
    prompt: str,
    model: str | None = None,
) -> BaseModel:
    """Envia PDF inteiro para Gemini e retorna instância Pydantic validada."""
    from google.genai import types as genai_types  # type: ignore

    client = gemini_client()
    model = model or settings.gemini_model_vision
    pdf_bytes = Path(pdf_path).read_bytes()

    resp = client.models.generate_content(
        model=model,
        contents=[
            genai_types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
            prompt,
        ],
        config=genai_types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=schema,
            temperature=0,
        ),
    )
    return resp.parsed  # type: ignore


def extrair_texto_pagina(
    pdf_path: Path,
    page_num: int,
    prompt: str,
    model: str | None = None,
) -> str:
    """Extrai texto/tabela de UMA página específica do PDF (rasterizada).

    Útil quando queremos o Gemini focado numa página sem custo do PDF todo.
    """
    import pdfplumber
    from google.genai import types as genai_types  # type: ignore

    client = gemini_client()
    model = model or settings.gemini_model_vision_cheap

    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[page_num - 1]
        img = page.to_image(resolution=200).original  # PIL Image
        import io

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        img_bytes = buf.getvalue()

    resp = client.models.generate_content(
        model=model,
        contents=[
            genai_types.Part.from_bytes(data=img_bytes, mime_type="image/png"),
            prompt,
        ],
        config=genai_types.GenerateContentConfig(temperature=0),
    )
    return resp.text or ""
