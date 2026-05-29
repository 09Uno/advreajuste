"""Parser genérico: abre PDF com pdfplumber, concatena texto e aplica regex PT-BR.

Cada parser específico (sulamerica, bradesco, etc.) pode estender ou sobrescrever.
"""
from __future__ import annotations

from pathlib import Path

import pdfplumber

from ..schemas import Boleto
from ._common import parse_brl, parse_competencia, parse_data_br, parse_pct


def extrair_texto_completo(pdf_path: Path) -> str:
    out: list[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        for p in pdf.pages:
            out.append(p.extract_text() or "")
    return "\n".join(out)


def parse_generic(pdf_path: Path, operadora: str) -> Boleto | None:
    texto = extrair_texto_completo(pdf_path)
    if not texto.strip():
        return None
    comp = parse_competencia(texto)
    valor = parse_brl(texto)
    if not comp or valor is None:
        return None
    return Boleto(
        competencia=comp,
        data_vencimento=parse_data_br(texto),
        valor_total=valor,
        reajuste_anual_pct=parse_pct(texto),
        operadora=operadora,
        beneficiarios=[],
    )
