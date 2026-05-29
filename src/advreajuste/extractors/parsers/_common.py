"""Regex PT-BR de resgate para valores/datas/competências."""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

RE_BRL = re.compile(r"R?\$?\s*(\d{1,3}(?:\.\d{3})*,\d{2})")
RE_PCT = re.compile(r"(-?\d{1,3}(?:[.,]\d+)?)\s*%")
RE_DATA = re.compile(r"(\d{2})/(\d{2})/(\d{4})")
RE_COMPETENCIA = re.compile(r"(\d{2})/(\d{4})")
RE_CPF = re.compile(r"(\d{3})\.?(\d{3})\.?(\d{3})[-.]?(\d{2})")
RE_CNPJ = re.compile(r"(\d{2})\.?(\d{3})\.?(\d{3})/?(\d{4})[-.]?(\d{2})")

MESES = {
    "janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4,
    "maio": 5, "junho": 6, "julho": 7, "agosto": 8, "setembro": 9,
    "outubro": 10, "novembro": 11, "dezembro": 12,
}


def parse_brl(texto: str) -> Decimal | None:
    m = RE_BRL.search(texto)
    if not m:
        return None
    v = m.group(1).replace(".", "").replace(",", ".")
    return Decimal(v)


def parse_pct(texto: str) -> Decimal | None:
    m = RE_PCT.search(texto)
    if not m:
        return None
    v = m.group(1).replace(",", ".")
    return Decimal(v) / Decimal("100")


def parse_data_br(texto: str) -> date | None:
    m = RE_DATA.search(texto)
    if not m:
        return None
    d, mth, y = (int(x) for x in m.groups())
    try:
        return date(y, mth, d)
    except ValueError:
        return None


def parse_competencia(texto: str) -> str | None:
    m = RE_COMPETENCIA.search(texto)
    if m:
        mes, ano = int(m.group(1)), int(m.group(2))
        if 1 <= mes <= 12 and 1900 <= ano <= 2100:
            return f"{ano:04d}-{mes:02d}"
    for nome, mes in MESES.items():
        r = re.search(rf"{nome}\s*(?:de\s*)?(\d{{4}})", texto, flags=re.I)
        if r:
            return f"{int(r.group(1)):04d}-{mes:02d}"
    return None
