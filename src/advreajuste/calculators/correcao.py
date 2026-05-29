"""Correção monetária — Tabela Prática TJSP reconstruída via BACEN.

Lei 14.905/2024 (vigente 30/08/2024) redefine juros legais = SELIC − IPCA.
Chave por data: pré-30/08/2024 = INPC + 1% a.m.; pós = SELIC líquida.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from .decimal_config import BRL, brl, to_decimal
from .indices import atualizar, carregar_serie, fator_acumulado

MARCO_LEI_14905 = date(2024, 8, 30)


def corrigir_indice(valor: Decimal, dt_origem: date, dt_destino: date, serie: str = "INPC") -> Decimal:
    return brl(atualizar(valor, dt_origem, dt_destino, serie))


def juros_mora_1pct_am(valor: Decimal, dt_origem: date, dt_destino: date) -> Decimal:
    """Juros legais pré-Lei 14.905/24 = 1% ao mês simples."""
    meses = (dt_destino.year - dt_origem.year) * 12 + (dt_destino.month - dt_origem.month)
    meses = max(meses, 0)
    return brl(to_decimal(valor) * Decimal("0.01") * meses)


def juros_selic_liquido(valor: Decimal, dt_origem: date, dt_destino: date) -> Decimal:
    """Juros pós-Lei 14.905/24 = SELIC − IPCA acumulados."""
    selic = carregar_serie("SELIC_MES")
    ipca = carregar_serie("IPCA")
    f_selic = fator_acumulado(selic, "SELIC_MES", dt_origem, dt_destino)
    f_ipca = fator_acumulado(ipca, "IPCA", dt_origem, dt_destino)
    liq = f_selic / f_ipca - Decimal("1")
    return brl(to_decimal(valor) * liq)


def corrigir_com_juros(valor: Decimal, dt_origem: date, dt_destino: date) -> dict:
    """Retorna dict com correção + juros separados, chaveado pela Lei 14.905/24."""
    corrigido = corrigir_indice(valor, dt_origem, dt_destino, "INPC")
    if dt_origem < MARCO_LEI_14905 <= dt_destino:
        # híbrido: correção + juros 1%a.m. até marco + Selic líquida depois
        j_pre = juros_mora_1pct_am(valor, dt_origem, MARCO_LEI_14905)
        j_pos = juros_selic_liquido(valor, MARCO_LEI_14905, dt_destino)
        juros = brl(j_pre + j_pos)
    elif dt_destino < MARCO_LEI_14905:
        juros = juros_mora_1pct_am(valor, dt_origem, dt_destino)
    else:
        juros = juros_selic_liquido(valor, dt_origem, dt_destino)
    return {
        "valor_original": brl(valor),
        "valor_corrigido": corrigido,
        "juros": juros,
        "total": brl(corrigido + juros - brl(valor) + brl(valor)),
        "regime": "pre-14905" if dt_destino < MARCO_LEI_14905 else "pos-14905",
    }
