"""Motor de SUBSTITUIÇÃO — metodologia conforme prática pericial brasileira.

Conforme áudio do cliente e planilha pericial de referência:

> "O que a gente verifica é qual foi a VARIAÇÃO no mês de aniversário.
> Ali eu encontro o percentual de reajuste que foi aplicado no contrato e
> eu vou SUBSTITUIR esse índice de reajuste contratual pelo índice que foi
> fixado pela ANS."

Portanto:
1. Para cada mês-aniversário do contrato, observar `pct_operadora = val[t]/val[t-1] - 1`.
2. Se `pct_operadora > pct_ans` → reajuste ABUSIVO → substituir por `pct_ans`.
3. Se `pct_operadora <= pct_ans` → operadora foi razoável → manter pago (delta = 0).
4. Reconstruir a série devida propagando a razão de substituição a partir daquele
   mês até o próximo aniversário.

Não se aplica faixa etária (orientação: "não é questionada nesse processo").
Não se usa base inicial × (1+ANS)^anos (metodologia incorreta: ignora downgrade
e variações reais, gerando inversões de sinal e superestimativas).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum

from dateutil.relativedelta import relativedelta

from ..extractors.schemas import Beneficiario, Contrato, ParcelaCobrada
from .ans import ans_cap
from .decimal_config import BRL, brl, to_decimal
from .faixa_etaria import idade_em


class TipoMes(str, Enum):
    NORMAL = "normal"
    ANIVERSARIO_ANS = "anual_ans"          # teto correto aplicado pela operadora
    ANIVERSARIO_ABUSIVO = "anual_abusivo"  # reajuste > teto ANS → substituir
    ANIVERSARIO_DOWNGRADE = "anual_downgrade"  # operadora reduziu → manter
    PRO_RATA = "pro_rata"                  # mensalidade fracionada (anomalia)
    ACERTO = "acerto"                      # mensalidade dobrada/triplicada (anomalia)


@dataclass(frozen=True)
class LinhaSubstituicao:
    competencia: str
    idade: int
    pago: Decimal
    reajuste_aplicado_pct: Decimal  # o que a operadora fez
    reajuste_devido_pct: Decimal    # o que o ANS permite
    tipo: TipoMes
    devido: Decimal
    diferenca: Decimal
    restituivel: bool


TOL = Decimal("0.002")  # tolerância de 0,2% para identificar reajuste vs flutuação

# Mensalidades fora destes limites em relação à mediana adjacente são anomalias
# (pro-rata fracionado, ou acerto/cobrança dobrada) — não contam como reajuste.
ANOMALIA_MIN = Decimal("0.65")   # se < 65% da mediana → pro-rata
ANOMALIA_MAX = Decimal("1.60")   # se > 160% da mediana → acerto/cobrança extra


def _first_day(y: int, m: int) -> date:
    return date(y, m, 1)


def _comp_to_date(comp: str) -> date:
    y, m = (int(x) for x in comp.split("-"))
    return _first_day(y, m)


def _detectar_anomalias(por_comp: dict[str, Decimal]) -> dict[str, TipoMes]:
    """Detecta mensalidades fora do padrão (pro-rata, acertos).

    Algoritmo em 2 passadas:
      1. Detecta anomalias usando vizinhos imediatos (±1)
      2. Re-detecta excluindo vizinhos que JÁ são anomalias
         (evita falso-positivo: 1514 vs 706 pro-rata vira "acerto")

    Limites: < 65% da menor adjacente → PRO_RATA; > 160% da maior → ACERTO.
    """
    comps = sorted(por_comp.keys())
    if len(comps) < 3:
        return {}

    def _detectar(excluir: set[str]) -> dict[str, TipoMes]:
        out: dict[str, TipoMes] = {}
        for i, comp in enumerate(comps):
            valor = por_comp[comp]
            vizinhos = []
            for j in (i - 1, i + 1):
                if 0 <= j < len(comps) and comps[j] not in excluir:
                    vizinhos.append(por_comp[comps[j]])
            if not vizinhos:
                continue
            min_viz = min(vizinhos)
            max_viz = max(vizinhos)
            if min_viz > 0 and valor / min_viz < ANOMALIA_MIN:
                out[comp] = TipoMes.PRO_RATA
            elif max_viz > 0 and valor / max_viz > ANOMALIA_MAX:
                out[comp] = TipoMes.ACERTO
        return out

    # Passada 1: detecção bruta
    anomalias_1 = _detectar(excluir=set())
    # Passada 2: refina excluindo vizinhos já marcados
    return _detectar(excluir=set(anomalias_1.keys()))


def motor_substituicao(
    beneficiario: Beneficiario,
    contrato: Contrato,
    cobrancas: list[ParcelaCobrada],
    dt_inicio: date | None = None,
    dt_fim: date | None = None,
    hoje: date | None = None,
    meses_restituicao: int = 36,
    projetar_ate: date | None = None,
) -> list[LinhaSubstituicao]:
    """Aplica metodologia de substituição do reajuste observado pelo ANS.

    Mês-aniversário vem de `contrato.mes_aniversario` (derivado da data_assinatura).
    """
    hoje = hoje or date.today()
    limite_rest = hoje - relativedelta(months=meses_restituicao)

    # Ordena cobranças por competência
    por_comp: dict[str, Decimal] = {c.competencia: to_decimal(c.valor_cobrado) for c in cobrancas}
    comps_ordenadas = sorted(por_comp.keys())
    if not comps_ordenadas:
        return []

    if dt_inicio is None:
        dt_inicio = _comp_to_date(comps_ordenadas[0])
    if dt_fim is None:
        dt_fim = _comp_to_date(comps_ordenadas[-1])

    # Projeção: se `projetar_ate` > última cobrança, preencher meses futuros com
    # o último valor pago (padrão pericial — contagem até a data de distribuição).
    if projetar_ate is not None and _comp_to_date(comps_ordenadas[-1]) < projetar_ate:
        ultimo_valor = por_comp[comps_ordenadas[-1]]
        py, pm = (int(x) for x in comps_ordenadas[-1].split("-"))
        py, pm = (py, pm + 1) if pm < 12 else (py + 1, 1)
        while date(py, pm, 1) <= projetar_ate:
            comp = f"{py:04d}-{pm:02d}"
            por_comp.setdefault(comp, ultimo_valor)
            pm += 1
            if pm > 12:
                pm, py = 1, py + 1
        dt_fim = max(dt_fim, projetar_ate)

    mes_aniv = contrato.mes_aniversario
    linhas: list[LinhaSubstituicao] = []

    # Pré-detecta anomalias (pro-rata, acertos) pra ignorar nos cálculos
    anomalias = _detectar_anomalias(por_comp)

    # Razão acumulada devido/pago. Aniversário abusivo reduz ratio; downgrade reseta.
    ratio = Decimal("1")
    pago_ant: Decimal | None = None  # último pago NÃO-anômalo

    y, m = dt_inicio.year, dt_inicio.month
    ey, em = dt_fim.year, dt_fim.month
    while (y, m) <= (ey, em):
        comp = f"{y:04d}-{m:02d}"
        pago = por_comp.get(comp)
        if pago is None:
            m += 1
            if m > 12:
                m, y = 1, y + 1
            continue

        idade = idade_em(beneficiario.data_nascimento, date(y, m, 1))
        eh_aniversario = m == mes_aniv
        tipo_anomalia = anomalias.get(comp)

        if tipo_anomalia:
            # Anomalia (pro-rata fracionado ou acerto/cobrança extra):
            # — marca a linha com tipo correto
            # — devido = pago (não vira diferença pro cálculo)
            # — NÃO atualiza pago_ant (próxima iteração compara com último não-anômalo)
            devido = brl(pago)
            diff = Decimal("0")
            linhas.append(LinhaSubstituicao(
                competencia=comp, idade=idade, pago=brl(pago),
                reajuste_aplicado_pct=Decimal("0"),
                reajuste_devido_pct=Decimal("0"),
                tipo=tipo_anomalia, devido=devido, diferenca=diff, restituivel=False,
            ))
            m += 1
            if m > 12:
                m, y = 1, y + 1
            continue

        if pago_ant is None:
            pct_operadora = Decimal("0")
            tipo = TipoMes.NORMAL
            reaj_dev = Decimal("0")
        else:
            pct_operadora = (pago - pago_ant) / pago_ant if pago_ant > 0 else Decimal("0")

            if eh_aniversario and abs(pct_operadora) > TOL:
                pct_ans = ans_cap(date(y, m, 1))
                if pct_operadora > pct_ans + TOL:
                    ratio = ratio * (Decimal("1") + pct_ans) / (Decimal("1") + pct_operadora)
                    tipo = TipoMes.ANIVERSARIO_ABUSIVO
                    reaj_dev = pct_ans
                elif pct_operadora < -TOL:
                    ratio = Decimal("1")
                    tipo = TipoMes.ANIVERSARIO_DOWNGRADE
                    reaj_dev = pct_operadora
                else:
                    tipo = TipoMes.ANIVERSARIO_ANS
                    reaj_dev = pct_operadora
            else:
                tipo = TipoMes.NORMAL
                reaj_dev = pct_operadora

        devido = brl(pago * ratio)
        diff = brl(pago - devido)
        rest = diff > 0 and date(y, m, 1) >= limite_rest

        linhas.append(LinhaSubstituicao(
            competencia=comp, idade=idade, pago=brl(pago),
            reajuste_aplicado_pct=pct_operadora,
            reajuste_devido_pct=reaj_dev,
            tipo=tipo, devido=devido, diferenca=diff, restituivel=rest,
        ))
        pago_ant = pago

        m += 1
        if m > 12:
            m, y = 1, y + 1

    return linhas


def totalizar_substituicao(linhas: list[LinhaSubstituicao]) -> dict:
    total_pago = sum((l.pago for l in linhas), Decimal("0"))
    total_devido = sum((l.devido for l in linhas), Decimal("0"))
    diff = brl(total_pago - total_devido)
    rest = sum((l.diferenca for l in linhas if l.restituivel), Decimal("0"))
    return {
        "total_pago": brl(total_pago),
        "total_devido": brl(total_devido),
        "diferenca": diff,
        "restituivel_simples": brl(rest),
        "restituivel_dobro_art42": brl(rest * 2),
        "n_meses": len(linhas),
        "n_aniversarios_abusivos": sum(1 for l in linhas if l.tipo == TipoMes.ANIVERSARIO_ABUSIVO),
        "n_downgrades": sum(1 for l in linhas if l.tipo == TipoMes.ANIVERSARIO_DOWNGRADE),
    }
