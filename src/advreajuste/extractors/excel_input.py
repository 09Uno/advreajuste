"""Leitura de XLSX do escritório/operadora usando python-calamine (rápido)."""
from __future__ import annotations

from pathlib import Path

import pandas as pd


def ler_xlsx(path: Path, sheet: str | int = 0) -> pd.DataFrame:
    try:
        return pd.read_excel(path, sheet_name=sheet, engine="calamine")
    except Exception:
        return pd.read_excel(path, sheet_name=sheet)
