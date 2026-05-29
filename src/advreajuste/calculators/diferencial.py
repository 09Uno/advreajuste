"""Motor de cálculo determinístico — por beneficiário, mês a mês.

Implementa STJ Tema 1016 (variação acumulada = produtório multiplicativo),
RN 563/2022 (mês subsequente), Súmula 91 TJSP (nulidade idoso),
prescrição trienal (art. 206 §3º IV CC).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable

from dateutil.relativedelta import relativedelta

from ..extractors.schemas import Beneficiario, Contrato, EventoTipo, ParcelaCobrada
from .ans import ans_cap
from .decimal_config import BRL, brl, to_decimal
from .faixa_etaria import FAIXAS_RN563, eh_idoso, faixa, idade_em


@dataclass(frozen=True)
class LinhaCalculo:
    competencia: str  # YYYY-MM
    idade: int
    faixa_idx: int
    tipo_evento: EventoTipo
    pct_aplicado: Decimal
    cobrada: Decimal
    devida: Decimal
    delta: Decimal
    restituivel: bool
    nulo_idoso: bool


def _period_range(dt_ini: date, dt_fim: date) -> Iterable[tuple[int, int]]:
    y, m = dt_ini.year, dt_ini.month
    end_y, end_m = dt_fim.year, dt_fim.month
    while (y, m) <= (end_y, end_m):
        yield y, m
        m += 1
        if m > 12:
            m, y = 1, y + 1


def _first_day(y: int, m: int) -> date:
    return date(y, m, 1)


def _last_day(y: int, m: int) -> date:
    ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
    return date(ny, nm, 1) - relativedelta(days=1)


def motor(
    beneficiario: Beneficiario,
    contrato: Contrato,
    cobrancas: list[ParcelaCobrada],
    pct_faixas: list[Decimal],
    dt_inicio: date,
    dt_fim: date,
    hoje: date | None = None,
    usa_teto_ans_para_falso_coletivo: bool = True,
) -> list[LinhaCalculo]:
    """Reconstrói série devida mês a mês e compara com cobranças reais.

    `pct_faixas` tem 10 posições, uma por faixa (pode vir vazia se contrato
    não especifica — nesse caso a faixa é marcada como violação em outro teste).
    """
    hoje = hoje or date.today()
    limite_restituicao = hoje - relativedelta(years=3)  # art. 206 §3º IV CC

    cobr_by_comp: dict[str, Decimal] = {c.competencia: to_decimal(c.valor_cobrado) for c in cobrancas}

    devida = to_decimal(beneficiario.mensalidade_base)
    # Estado inicial
    idade_inicial = idade_em(beneficiario.data_nascimento, _last_day(dt_inicio.year, dt_inicio.month))
    faixa_anterior = faixa(idade_inicial)

    linhas: list[LinhaCalculo] = []
    for y, m in _period_range(dt_inicio, dt_fim):
        ref = _last_day(y, m)
        idade = idade_em(beneficiario.data_nascimento, ref)
        f = faixa(idade)

        tipo = EventoTipo.DESCONHECIDO
        pct_aplicado = Decimal("0")
        nulo_idoso = False

        # (1) Faixa etária — incide no mês SUBSEQUENTE ao aniversário
        mudou_faixa = f != faixa_anterior
        if mudou_faixa and FAIXAS_RN563[f] in {19, 24, 29, 34, 39, 44, 49, 54, 59}:
            if eh_idoso(idade):
                nulo_idoso = True  # Súmula 91 TJSP / Estatuto do Idoso
            elif pct_faixas and len(pct_faixas) == 10:
                pct_aplicado = pct_faixas[f]
                devida = brl(devida * (Decimal("1") + pct_aplicado))
                tipo = EventoTipo.FAIXA_ETARIA

        # (2) Reajuste anual no aniversário do contrato
        if m == contrato.mes_aniversario and (y, m) != (dt_inicio.year, dt_inicio.month):
            if usa_teto_ans_para_falso_coletivo and contrato.falso_coletivo:
                pct_ans = ans_cap(ref)
                if tipo == EventoTipo.DESCONHECIDO:
                    pct_aplicado = pct_ans
                devida = brl(devida * (Decimal("1") + pct_ans))
                tipo = EventoTipo.ANUAL_ANS

        cobrada = cobr_by_comp.get(f"{y:04d}-{m:02d}", devida)
        delta = brl(cobrada - devida)
        restituivel = delta > 0 and ref >= limite_restituicao

        linhas.append(
            LinhaCalculo(
                competencia=f"{y:04d}-{m:02d}",
                idade=idade,
                faixa_idx=f,
                tipo_evento=tipo,
                pct_aplicado=pct_aplicado,
                cobrada=brl(cobrada),
                devida=brl(devida),
                delta=delta,
                restituivel=restituivel,
                nulo_idoso=nulo_idoso,
            )
        )
        faixa_anterior = f

    return linhas


def totalizar(linhas: list[LinhaCalculo]) -> dict:
    total_pago = sum((l.cobrada for l in linhas), Decimal("0"))
    total_devido = sum((l.devida for l in linhas), Decimal("0"))
    total_delta = brl(total_pago - total_devido)
    restituivel = sum((l.delta for l in linhas if l.restituivel), Decimal("0"))
    return {
        "total_pago": brl(total_pago),
        "total_devido": brl(total_devido),
        "diferenca": total_delta,
        "restituivel_simples": brl(restituivel),
        "restituivel_dobro_art42": brl(restituivel * 2),  # CDC art. 42 §ún (má-fé)
        "n_meses": len(linhas),
    }


def classificar_reajuste_reverso(
    pct_observado: Decimal,
    mes: int,
    mes_aniversario_contrato: int,
    pct_ans_esperado: Decimal,
    pct_faixa_esperado: Decimal | None,
    tol: Decimal = Decimal("0.005"),
) -> EventoTipo:
    """Classifica um reajuste observado a partir do pct cobrado t/t-1."""
    if pct_observado < Decimal("-0.01"):
        return EventoTipo.DOWNGRADE
    if pct_faixa_esperado is not None and abs(pct_observado - pct_faixa_esperado) < tol:
        return EventoTipo.FAIXA_ETARIA
    if mes == mes_aniversario_contrato:
        if abs(pct_observado - pct_ans_esperado) < tol:
            return EventoTipo.ANUAL_ANS
        if pct_observado > pct_ans_esperado + tol:
            return EventoTipo.ANUAL_CONTRATO
    return EventoTipo.DESCONHECIDO
