"""Faixas etárias RN 563/2022 (substitui RN 63/2003).

10 faixas. Incidência no **mês subsequente** ao aniversário. Restrições:
- variação acumulada 7ª–10ª ≤ variação acumulada 1ª–7ª (art. 3º II RN 63)
- última faixa ≤ 6× a primeira
- Súmula 91 TJSP / Estatuto do Idoso: reajuste de faixa a partir de 60 anos é NULO
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from functools import reduce

from dateutil.relativedelta import relativedelta

# Limites inferiores de cada faixa (RN 563/2022)
FAIXAS_RN563: list[int] = [0, 19, 24, 29, 34, 39, 44, 49, 54, 59]


def faixa(idade: int) -> int:
    """Retorna o índice (0–9) da faixa etária. Negativos → 0."""
    idade = max(0, int(idade))
    return max(i for i, lim in enumerate(FAIXAS_RN563) if idade >= lim)


def idade_em(dt_nasc: date, ref: date) -> int:
    return relativedelta(ref, dt_nasc).years


def mes_subsequente_ao_aniversario(dt_nasc: date, comp_ano: int, comp_mes: int) -> bool:
    """True se (comp_ano, comp_mes) é o mês SEGUINTE ao aniversário no ano dado."""
    mes_aniv = dt_nasc.month
    alvo_mes = mes_aniv % 12 + 1
    alvo_ano = comp_ano + (1 if mes_aniv == 12 else 0)
    return comp_ano == alvo_ano and comp_mes == alvo_mes


def valida_restricoes(pct_faixas: list[Decimal]) -> list[str]:
    """Retorna lista de violações normativas. Vazia = contrato válido."""
    erros: list[str] = []
    if len(pct_faixas) != 10:
        erros.append(f"Devem existir 10 percentuais de faixa (recebido {len(pct_faixas)}).")
        return erros
    um = Decimal("1")

    def acumulado(inicio: int, fim: int) -> Decimal:
        slice_ = pct_faixas[inicio : fim + 1]
        return reduce(lambda acc, r: acc * (um + r), slice_, um) - um

    acum_1_7 = acumulado(0, 6)
    acum_7_10 = acumulado(6, 9)
    if acum_7_10 > acum_1_7:
        erros.append(
            f"Variação acumulada 7ª→10ª ({acum_7_10:.4%}) > 1ª→7ª ({acum_1_7:.4%}) "
            "— viola art. 3º II RN 63/2003."
        )
    if pct_faixas[0] > 0 and pct_faixas[9] > pct_faixas[0] * 6:
        erros.append(
            f"Última faixa ({pct_faixas[9]:.4%}) > 6× a primeira ({pct_faixas[0]:.4%})."
        )
    return erros


def eh_idoso(idade: int) -> bool:
    """Estatuto do Idoso art. 15 §3º — proibido reajuste de faixa ≥ 60 anos."""
    return idade >= 60
