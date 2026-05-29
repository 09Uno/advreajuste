"""Fallback inteligente: aciona Vision apenas onde regex falhou.

Estratégia:
  1. Extrair com parser template/regex (custo zero).
  2. Para cada beneficiário esperado, detectar competências faltantes.
  3. Se faltantes > limiar, enviar PDFs para Vision (Gemini primário).
  4. Mesclar resultados.

Isso mantém o custo mínimo: Vision só entra quando realmente necessário.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Literal

from loguru import logger
from pydantic import BaseModel, Field

from ..llm.vision import extrair_com_vision
from .parsers.sulamerica_pme import LinhaPagamento


class LinhaVision(BaseModel):
    competencia: str = Field(pattern=r"^\d{4}-\d{2}$")
    nome: str
    cpf: str | None = None
    data_nascimento: str  # YYYY-MM-DD
    parentesco: Literal["TITULAR", "CONJUGE", "FILHOS", "AGREGADO", "DEPENDENTE"]
    valor: float


class FaturaVision(BaseModel):
    """Schema estruturado que Gemini/Claude vão preencher."""
    operadora: str
    apolice: str | None = None
    estipulante: str | None = None
    linhas: list[LinhaVision] = Field(
        description=(
            "Uma entrada por beneficiário por competência. Se o PDF tem 3 meses "
            "de cobrança e 6 vidas, retorne 18 linhas. Extrair TODOS os beneficiários, "
            "incluindo dependentes. Valor é o prêmio individual de cada vida em R$."
        )
    )


PROMPT_FALLBACK = """Você é um extrator determinístico de faturas de plano de saúde
Sul América (formato "Saúde OnLine" / "PME Faturamento").

Para CADA beneficiário em CADA competência (mês) do documento, extraia:
  - competencia: YYYY-MM (mês de referência na seção "Período de Competência")
  - nome: nome completo em CAIXA ALTA
  - cpf: se disponível (dígitos apenas); omita se não estiver
  - data_nascimento: YYYY-MM-DD
  - parentesco: TITULAR | CONJUGE | FILHOS | AGREGADO | DEPENDENTE
  - valor: o prêmio individual em R$ (número decimal; ex.: 988.23 para R$ 988,23)

NÃO inclua totais de família ou total geral. APENAS linhas individuais de beneficiários.
NÃO inclua IOF.
"""


def detectar_faltantes(
    linhas_regex: list[LinhaPagamento],
    cpfs_esperados: list[str],
    competencia_esperada_inicio: str,
    competencia_esperada_fim: str,
) -> dict[str, list[str]]:
    """Para cada CPF, retorna lista de competências faltantes."""
    tem_por_cpf: dict[str, set[str]] = {cpf: set() for cpf in cpfs_esperados}
    for l in linhas_regex:
        if l.cpf in tem_por_cpf:
            tem_por_cpf[l.cpf].add(l.competencia)

    def range_meses(ini: str, fim: str) -> list[str]:
        y, m = (int(x) for x in ini.split("-"))
        ey, em = (int(x) for x in fim.split("-"))
        out = []
        while (y, m) <= (ey, em):
            out.append(f"{y:04d}-{m:02d}")
            m += 1
            if m > 12:
                m, y = 1, y + 1
        return out

    todos = set(range_meses(competencia_esperada_inicio, competencia_esperada_fim))
    return {cpf: sorted(todos - tem_por_cpf[cpf]) for cpf in cpfs_esperados}


def aplicar_vision_em_pdf(
    pdf_path: Path, threshold_faltantes: int = 3,
) -> list[LinhaPagamento]:
    """Envia o PDF inteiro para Vision e retorna LinhaPagamento compatível."""
    logger.info("Vision fallback sobre {}", pdf_path.name)
    resultado: FaturaVision = extrair_com_vision(
        pdf_path, FaturaVision, PROMPT_FALLBACK,
    )
    saida: list[LinhaPagamento] = []
    for l in resultado.linhas:
        try:
            nasc = date.fromisoformat(l.data_nascimento)
        except Exception:
            continue
        saida.append(LinhaPagamento(
            competencia=l.competencia, nome=l.nome.title(),
            cpf=(l.cpf or "").replace(".", "").replace("-", "").strip(),
            data_nascimento=nasc,
            parentesco=l.parentesco,
            valor=Decimal(str(l.valor)),
            origem_pdf=pdf_path.name,
            pagina=0,
        ))
    return saida


def extrair_com_fallback(
    pdfs: list[Path],
    linhas_regex: list[LinhaPagamento],
    cpfs_esperados: list[str] | None = None,
    max_pct_faltantes: float = 0.10,
) -> list[LinhaPagamento]:
    """Se regex cobre pelo menos (1 - max_pct_faltantes) do esperado,
    retorna só linhas_regex. Senão, invoca Vision nos PDFs.
    """
    if cpfs_esperados is None:
        return linhas_regex

    comps = sorted({l.competencia for l in linhas_regex})
    if not comps:
        logger.warning("Regex retornou zero — aplicando Vision em todos PDFs")
        out = list(linhas_regex)
        for pdf in pdfs:
            try:
                out.extend(aplicar_vision_em_pdf(pdf))
            except Exception as e:
                logger.error("Vision falhou em {}: {}", pdf.name, e)
        return out

    faltantes_por_cpf = detectar_faltantes(linhas_regex, cpfs_esperados, comps[0], comps[-1])
    total_esperado = (len(cpfs_esperados) *
                      (len(comps) if comps else 0))
    total_faltantes = sum(len(v) for v in faltantes_por_cpf.values())
    pct = total_faltantes / total_esperado if total_esperado else 0

    if pct <= max_pct_faltantes:
        logger.info("Regex cobre {:.1f}% — Vision dispensado", (1 - pct) * 100)
        return linhas_regex

    logger.info("Regex faltou {:.1f}% — acionando Vision", pct * 100)
    out = list(linhas_regex)
    # dedup-key
    keys = {(l.competencia, l.cpf) for l in linhas_regex}
    for pdf in pdfs:
        try:
            extra = aplicar_vision_em_pdf(pdf)
            for l in extra:
                if (l.competencia, l.cpf) not in keys:
                    out.append(l)
                    keys.add((l.competencia, l.cpf))
        except Exception as e:
            logger.error("Vision falhou em {}: {}", pdf.name, e)
    return out
