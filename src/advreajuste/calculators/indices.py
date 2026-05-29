"""Índices financeiros via BACEN SGS com cache local."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd

from ..config import settings
from .decimal_config import to_decimal

SGS_CODES = {
    "IPCA": 433,
    "INPC": 188,
    "IGP-M": 189,
    "SELIC_DIA": 11,
    "SELIC_MES": 4390,
}


def _cache_path(serie: str) -> Path:
    d = settings.data_dir / "indices"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{serie}.parquet"


def baixar_serie(serie: str, start: str = "1994-07-01") -> pd.DataFrame:
    from bcb import sgs

    code = SGS_CODES[serie]
    df = sgs.get({serie: code}, start=start)
    df.to_parquet(_cache_path(serie))
    return df


def carregar_serie(serie: str, start: str = "1994-07-01", refresh: bool = False) -> pd.DataFrame:
    p = _cache_path(serie)
    if refresh or not p.exists():
        return baixar_serie(serie, start=start)
    return pd.read_parquet(p)


def fator_acumulado(serie_df: pd.DataFrame, col: str, dt_origem: date, dt_destino: date) -> Decimal:
    df = serie_df.copy()
    df["fator"] = (1 + df[col] / 100).cumprod()
    ser = df["fator"]
    idx_origem = ser.index.asof(pd.Timestamp(dt_origem))
    idx_dest = ser.index.asof(pd.Timestamp(dt_destino))
    if pd.isna(idx_origem) or pd.isna(idx_dest):
        raise ValueError(f"Fator indisponível para {dt_origem} → {dt_destino}")
    return to_decimal(ser.loc[idx_dest]) / to_decimal(ser.loc[idx_origem])


def atualizar(
    valor: Decimal,
    dt_origem: date,
    dt_destino: date,
    serie: str = "INPC",
) -> Decimal:
    df = carregar_serie(serie)
    f = fator_acumulado(df, serie, dt_origem, dt_destino)
    return (to_decimal(valor) * f)
