"""Tabela histórica de reajuste ANS individual (pessoa física).

Percentuais de vigência **maio do ano Y até abril do ano Y+1**, aplicados no
mês-aniversário do contrato. Fonte oficial: XLSX ANS
`historico-reajuste-variacao-custo-pessoa-fisica`.

IMPORTANTE 2000–2001: fontes secundárias divergem. Confirmar antes de peticionar.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd

from .decimal_config import to_decimal


# Ano inicial da vigência (maio/Y → abril/Y+1)
ANS_INDIVIDUAL: dict[int, Decimal] = {
    2000: Decimal("0.0542"),
    2001: Decimal("0.0871"),
    2002: Decimal("0.0769"),
    2003: Decimal("0.0927"),
    2004: Decimal("0.1175"),
    2005: Decimal("0.1169"),
    2006: Decimal("0.0889"),
    2007: Decimal("0.0576"),
    2008: Decimal("0.0548"),
    2009: Decimal("0.0676"),
    2010: Decimal("0.0673"),
    2011: Decimal("0.0769"),
    2012: Decimal("0.0793"),
    2013: Decimal("0.0904"),
    2014: Decimal("0.0965"),
    2015: Decimal("0.1355"),
    2016: Decimal("0.1357"),
    2017: Decimal("0.1355"),
    2018: Decimal("0.1000"),
    2019: Decimal("0.0735"),
    2020: Decimal("0.0814"),
    2021: Decimal("-0.0819"),
    2022: Decimal("0.1550"),
    2023: Decimal("0.0963"),
    2024: Decimal("0.0691"),
    2025: Decimal("0.0606"),
}


def ano_vigencia(dt: date) -> int:
    """Retorna o ano-inicial da vigência ANS aplicável ao mês `dt`.

    Vigência vai de maio/Y até abril/Y+1. Jan/Y+1 ainda usa percentual do Y.
    """
    return dt.year if dt.month >= 5 else dt.year - 1


def ans_cap(dt: date) -> Decimal:
    """Teto ANS individual aplicável no mês-aniversário `dt`."""
    return ANS_INDIVIDUAL.get(ano_vigencia(dt), Decimal("0"))


def carregar_xlsx_oficial(path: Path) -> dict[int, Decimal]:
    """Opcional: carrega XLSX baixado do site ANS para sobrescrever a tabela."""
    df = pd.read_excel(path)
    out: dict[int, Decimal] = {}
    for _, row in df.iterrows():
        for col in df.columns:
            try:
                ano = int(str(row.get(col, "")).split("/")[0])
            except Exception:
                continue
            if 2000 <= ano <= 2100:
                pct_col = next(
                    (c for c in df.columns if "percent" in str(c).lower() or "%" in str(c)), None
                )
                if pct_col:
                    out[ano] = to_decimal(row[pct_col]) / Decimal("100")
                break
    return out
