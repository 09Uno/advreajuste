"""Roteamento por cabeçalho: extrai primeira página e tenta regex da operadora."""
from __future__ import annotations

import re
from pathlib import Path

import pdfplumber

from ..schemas import Boleto

ROUTES: dict[str, list[str]] = {
    "sulamerica": [r"SUL\s*AM[EÉ]RICA", r"\bSALU[ÇC]\w*\b"],
    "bradesco": [r"BRADESCO\s+SA[UÚ]DE"],
    "amil": [r"\bAMIL\b", r"AMIL\s+ASSIST[ÊE]NCIA"],
    "unimed": [r"\bUNIMED\b"],
    "hapvida": [r"\bHAPVIDA\b", r"NOTREDAME\s+INTERMEDICA"],
}


def extrair_texto_primeira_pagina(pdf_path: Path) -> str:
    with pdfplumber.open(pdf_path) as pdf:
        if not pdf.pages:
            return ""
        return pdf.pages[0].extract_text() or ""


def detect_operadora(pdf_path: Path) -> str | None:
    txt = extrair_texto_primeira_pagina(pdf_path).upper()
    for op, pats in ROUTES.items():
        for pat in pats:
            if re.search(pat, txt, flags=re.I):
                return op
    return None


def parse(pdf_path: Path) -> Boleto | None:
    op = detect_operadora(pdf_path)
    if not op:
        return None
    from . import sulamerica, bradesco, amil, unimed, hapvida

    mapping = {
        "sulamerica": sulamerica.parse,
        "bradesco": bradesco.parse,
        "amil": amil.parse,
        "unimed": unimed.parse,
        "hapvida": hapvida.parse,
    }
    fn = mapping.get(op)
    if not fn:
        return None
    try:
        return fn(pdf_path)
    except Exception:
        return None
