"""Camada 1: extração barata por template + regex."""
from __future__ import annotations

from pathlib import Path

from .parsers.router import parse as route_parse
from .schemas import Boleto


def extrair(pdf_path: Path) -> tuple[Boleto | None, float]:
    """Retorna (boleto, confianca). confianca ∈ [0,1]."""
    b = route_parse(pdf_path)
    if b is None:
        return None, 0.0
    conf = 0.5
    if b.valor_total > 0:
        conf += 0.25
    if b.data_vencimento:
        conf += 0.1
    if b.reajuste_anual_pct is not None:
        conf += 0.15
    return b, min(conf, 1.0)
