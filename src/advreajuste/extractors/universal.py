"""Extrator UNIVERSAL via Vision API (Gemini 2.5 Flash).

Funciona em QUALQUER operadora de plano de saúde brasileira (Sul América,
Bradesco, Amil, Unimed, Hapvida, Notre Dame, Care Plus, Porto Seguro, Allianz,
Cassi, GEAP, etc.) sem precisar de regex específico por formato.

IMPORTANTE: o contrato do plano de saúde NÃO entra no cálculo. A metodologia
do STJ Tema 1016 olha a variação da mensalidade entre dois meses de
aniversário consecutivos (X → Y), encontra o reajuste contratual aplicado e
substitui pelo teto fixado pela ANS naquele ano. Tudo isso sai dos
demonstrativos — o contrato em si é dispensável (e nem é anexado às ações).

Custo típico (Gemini 2.5 Flash, abril/2026):
  - ~R$ 0,01 por PDF de 1-2 páginas
  - ~R$ 0,15-0,30 por caso completo (60-100 PDFs)

Schema universal: cada PDF retorna 0..N linhas {competencia, beneficiario,
valor} + metadados de cabeçalho (operadora, apólice, estipulante). O pipeline
depois agrupa por beneficiário e por competência.
"""
from __future__ import annotations

import base64
import re
import time
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Callable, Literal

from loguru import logger
from pydantic import BaseModel, Field

from ..llm.gemini_client import gemini_client
from ..config import settings


# Rate limit do Gemini 2.5 Flash free tier: 20 RPM, 250 RPD.
# Mantemos um intervalo mínimo entre chamadas pra ficar abaixo do limite.
# 3.5s × 20 = 70s → ~17 RPM (margem de segurança).
GEMINI_FREE_TIER_MIN_INTERVAL = 3.5
_ultima_chamada = [0.0]  # mutável compartilhado


def _throttle():
    """Aguarda o suficiente pra respeitar 20 RPM no tier free."""
    elapsed = time.time() - _ultima_chamada[0]
    if elapsed < GEMINI_FREE_TIER_MIN_INTERVAL:
        time.sleep(GEMINI_FREE_TIER_MIN_INTERVAL - elapsed)
    _ultima_chamada[0] = time.time()


def _parse_retry_delay(err_msg: str) -> float:
    """Extrai 'Please retry in 47.78s' do erro 429."""
    m = re.search(r"retry in ([\d.]+)s", err_msg, re.IGNORECASE)
    if m:
        return float(m.group(1)) + 1
    return 60.0


# ─────────────────────── Schemas ───────────────────────

class LinhaCobranca(BaseModel):
    """Uma linha de cobrança individual em um demonstrativo."""
    competencia: str = Field(
        description="Mês de referência da cobrança no formato YYYY-MM. "
                     "Use 'Período de Competência', 'Mês de Referência' ou 'Vencimento'. "
                     "Se houver múltiplos meses no PDF, retorne uma linha por mês.",
    )
    nome_beneficiario: str = Field(
        description="Nome completo do beneficiário em CAIXA ALTA. "
                     "Pode ser titular, cônjuge, filho ou agregado.",
    )
    cpf: str | None = Field(
        default=None,
        description="CPF do beneficiário (apenas dígitos). Null se não constar.",
    )
    data_nascimento: str | None = Field(
        default=None,
        description="Data de nascimento no formato YYYY-MM-DD. Null se não constar.",
    )
    parentesco: Literal["TITULAR", "CONJUGE", "FILHO", "AGREGADO", "DEPENDENTE"] | None = Field(
        default=None,
        description="Grau de parentesco com o titular.",
    )
    valor: float = Field(
        description="Valor da mensalidade DESSE beneficiário em R$ (ex: 988.23). "
                     "Se o documento mostra apenas valor total da família, divida "
                     "proporcionalmente OU retorne o total e marque parentesco='TITULAR'.",
    )
    data_inicio_vigencia: str | None = Field(
        default=None,
        description="Data de início da vigência DESTE beneficiário no contrato, "
                     "no formato YYYY-MM-DD. Aparece em colunas como "
                     "'Início Vigência', 'Data de Inclusão', 'Data de Adesão'. "
                     "Null se não constar.",
    )


class DadosCabecalho(BaseModel):
    """Metadados extraídos do CABEÇALHO do demonstrativo (não do contrato).

    O contrato do plano de saúde NÃO é usado nos cálculos — apenas os
    demonstrativos importam. Estes campos servem só para rotular a
    planilha/minuta.
    """
    operadora: str | None = Field(
        default=None,
        description="Nome da operadora identificada no cabeçalho do demonstrativo "
                     "(Sul América, Bradesco, Amil, Unimed, Hapvida, etc.)",
    )
    apolice: str | None = Field(
        default=None,
        description="Número da apólice impresso no demonstrativo.",
    )
    estipulante: str | None = Field(
        default=None,
        description="Razão social da empresa estipulante (em planos coletivos).",
    )
    tipo_plano: Literal["individual", "familiar", "coletivo_empresarial",
                          "coletivo_adesao", "desconhecido"] | None = Field(
        default=None,
    )


# Alias retrocompatível (código antigo importava `DadosContrato`)
DadosContrato = DadosCabecalho


class ExtracaoPDF(BaseModel):
    """Resultado da extração de um único PDF (que pode conter 1+ competências)."""
    contrato: DadosCabecalho = Field(
        description="Metadados do cabeçalho do demonstrativo "
                     "(operadora, apólice, estipulante).",
    )
    cobrancas: list[LinhaCobranca] = Field(
        description="Lista de TODAS as cobranças encontradas. Se há 3 meses × "
                     "5 beneficiários no PDF, retorne 15 linhas. NUNCA retorne "
                     "linhas de TOTAL DA FAMÍLIA ou TOTAL GERAL — apenas linhas "
                     "individuais por beneficiário.",
    )


# ─────────────────────── Prompt ───────────────────────

PROMPT_EXTRACAO = """Você é um extrator determinístico de demonstrativos de pagamento
de planos de saúde brasileiros. Extraia TODAS as linhas individuais de cobrança.

REGRAS CRÍTICAS:
1. **Nunca invente dados** — se um campo não aparece, retorne null.
2. **Uma linha por beneficiário por competência**. Se o PDF mostra 3 meses
   (ex: jan/2024, fev/2024, mar/2024) com 4 vidas cada, retorne 12 linhas.
3. **NÃO inclua** linhas de "Total da Família", "Total Geral", "IOF",
   "Coparticipação", "Subtotal" etc. APENAS linhas de pessoas.
4. **Valor**: o prêmio individual em R$ daquela vida naquele mês.
   - 1.234,56 → 1234.56
   - R$ 988,23 → 988.23
5. **Competência**: YYYY-MM do mês de referência (não do vencimento).
   Ex: "Período de Competência: 23/01/2025 a 22/02/2025" → "2025-01"
6. **Operadora**: identifique pelo cabeçalho do documento. Se for boleto
   bancário simples sem identificação clara, retorne null.
7. **data_inicio_vigencia**: extraia DA TABELA, coluna "Início Vigência" /
   "Data de Inclusão" / "Adesão". Esta data é POR BENEFICIÁRIO — não confunda
   com o vencimento do boleto. Se o documento não tem essa coluna, deixe null.

FORMATOS COMUNS:
- Sul América (Saúde OnLine PME): tabela com matrícula, plano, nome, dn, idade,
  parentesco, vigência, premio
- Bradesco Saúde: nome, CPF, data nasc, valor mensal
- Amil: cabeçalho com apólice + linhas de beneficiário
- Unimed: pode ter formato cooperativa específico por região
- Hapvida/Notre Dame: lista simples com valor por vida

CASOS ESPECIAIS:
- Se o PDF é apenas comprovante bancário (DDA, débito automático) sem
  detalhamento por beneficiário, retorne `cobrancas: []` e use o valor total
  no campo do titular se houver indicação de quem é o titular.
- Se o PDF é fatura de plano INDIVIDUAL (1 vida só), retorne 1 linha com
  parentesco='TITULAR'.
- Se houver ajuste/desconto/multa, NÃO inclua na cobrança individual —
  considere apenas a mensalidade base de cada vida.
"""


# ─────────────────────── Função principal ───────────────────────

def extrair_pdf(
    pdf_path: Path,
    model: str | None = None,
    max_retries: int = 4,
) -> ExtracaoPDF:
    """Extrai dados estruturados de um único PDF via Gemini Vision.

    Com throttle (≤20 RPM) + retry exponencial em 429 RESOURCE_EXHAUSTED.
    """
    from google.genai import types as genai_types

    client = gemini_client()
    model = model or settings.gemini_model_vision
    pdf_bytes = Path(pdf_path).read_bytes()

    last_err = None
    for tentativa in range(max_retries):
        _throttle()
        try:
            resp = client.models.generate_content(
                model=model,
                contents=[
                    genai_types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
                    PROMPT_EXTRACAO,
                ],
                config=genai_types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=ExtracaoPDF,
                    temperature=0,
                ),
            )
            parsed = resp.parsed
            if parsed is None:
                import json as _json
                try:
                    return ExtracaoPDF.model_validate(_json.loads(resp.text))
                except Exception as e:
                    logger.error("Gemini retornou resposta não parseável em {}: {}",
                                 pdf_path.name, e)
                    return ExtracaoPDF(contrato=DadosCabecalho(), cobrancas=[])
            return parsed
        except Exception as e:
            msg = str(e)
            last_err = e
            if "429" in msg or "RESOURCE_EXHAUSTED" in msg or "quota" in msg.lower():
                wait = _parse_retry_delay(msg)
                logger.warning("Rate limit em {} — aguardando {:.0f}s (tentativa {}/{})",
                               pdf_path.name, wait, tentativa + 1, max_retries)
                time.sleep(wait)
                continue
            # Outros erros: re-raise direto
            raise
    # Esgotaram as tentativas
    logger.error("Falha em {} após {} tentativas: {}", pdf_path.name, max_retries, last_err)
    return ExtracaoPDF(contrato=DadosCabecalho(), cobrancas=[])


def extrair_pasta(
    pdfs: list[Path],
    model: str | None = None,
    progress_cb: Callable[[int, int, str], None] | None = None,
) -> dict:
    """Extrai todos os PDFs e consolida em estrutura compatível com pipeline.

    Retorna dict no MESMO formato que `sulamerica_pme.agrupar_beneficiarios`,
    pra reaproveitar o pipeline existente.

    `progress_cb(i, total, nome_pdf)` é chamado a cada PDF — útil pra UI.
    """
    todas_linhas: list[dict] = []
    cabecalhos_detectados: list[DadosCabecalho] = []
    erros: list[str] = []

    for i, pdf in enumerate(pdfs, start=1):
        logger.info("[{}/{}] Extraindo {}", i, len(pdfs), pdf.name)
        if progress_cb:
            try:
                progress_cb(i, len(pdfs), pdf.name)
            except Exception:
                pass
        try:
            ex = extrair_pdf(pdf, model=model)
            if ex.contrato.operadora:
                cabecalhos_detectados.append(ex.contrato)
            if not ex.cobrancas:
                # Vision retornou vazio (depois de retries) — registra como erro
                erros.append(f"{pdf.name}: 0 cobranças extraídas")
            for c in ex.cobrancas:
                todas_linhas.append({
                    "competencia": c.competencia,
                    "nome": c.nome_beneficiario.strip().title(),
                    "cpf": (c.cpf or "").replace(".", "").replace("-", "").strip(),
                    "data_nascimento": c.data_nascimento,
                    "parentesco": (c.parentesco or "TITULAR").upper(),
                    "valor": Decimal(str(c.valor)),
                    "data_inicio_vigencia": c.data_inicio_vigencia,
                    "origem_pdf": pdf.name,
                })
        except Exception as e:
            logger.error("Falha em {}: {}", pdf.name, e)
            erros.append(f"{pdf.name}: {e}")

    # Reconcilia CPFs faltantes via nome → CPF visto em outros PDFs
    nome_to_cpf: dict[str, str] = {}
    for l in todas_linhas:
        if l["cpf"] and l["nome"].upper() not in nome_to_cpf:
            nome_to_cpf[l["nome"].upper()] = l["cpf"]
    for l in todas_linhas:
        if not l["cpf"] and l["nome"].upper() in nome_to_cpf:
            l["cpf"] = nome_to_cpf[l["nome"].upper()]

    # Agrupa por (cpf ou nome) → estrutura `grupos` esperada pelo pipeline
    grupos: dict[str, dict] = {}
    for l in todas_linhas:
        chave = l["cpf"] or l["nome"].upper()
        if chave not in grupos:
            # Parse data nascimento — se vier null, usa um fallback
            try:
                nasc = date.fromisoformat(l["data_nascimento"]) if l["data_nascimento"] else None
            except Exception:
                nasc = None
            try:
                inicio = date.fromisoformat(l["data_inicio_vigencia"]) if l.get("data_inicio_vigencia") else None
            except Exception:
                inicio = None
            grupos[chave] = {
                "nome": l["nome"],
                "cpf": l["cpf"],
                "data_nascimento": nasc or date(1970, 1, 1),
                "data_inicio_vigencia": inicio,
                "parentesco": l["parentesco"],
                "cobrancas": [],
            }
        # Atualiza cpf se descoberto depois
        if not grupos[chave]["cpf"] and l["cpf"]:
            grupos[chave]["cpf"] = l["cpf"]
        # Atualiza inicio vigência se ainda não tem
        if not grupos[chave].get("data_inicio_vigencia") and l.get("data_inicio_vigencia"):
            try:
                grupos[chave]["data_inicio_vigencia"] = date.fromisoformat(l["data_inicio_vigencia"])
            except Exception:
                pass
        grupos[chave]["cobrancas"].append({
            "competencia": l["competencia"],
            "valor": l["valor"],
            "nome": l["nome"],
            "cpf": l["cpf"],
            "parentesco": l["parentesco"],
            "origem_pdf": l["origem_pdf"],
            "data_nascimento": grupos[chave]["data_nascimento"],
            "pagina": 0,
        })

    # Detecta operadora majoritária
    operadora_predominante = None
    if cabecalhos_detectados:
        from collections import Counter
        ops = [c.operadora for c in cabecalhos_detectados if c.operadora]
        if ops:
            operadora_predominante = Counter(ops).most_common(1)[0][0]

    apolice_detectada = next(
        (c.apolice for c in cabecalhos_detectados if c.apolice), None
    )
    estipulante_detectado = next(
        (c.estipulante for c in cabecalhos_detectados if c.estipulante), None
    )

    mes_aniv_det, evidencias = detectar_mes_aniversario(grupos)
    inicio_vig_det = detectar_inicio_vigencia(grupos)

    return {
        "tipo": "universal_vision",
        "n_pdfs": len(pdfs),
        "n_linhas": len(todas_linhas),
        "n_beneficiarios": len(grupos),
        "operadora_detectada": operadora_predominante,
        "apolice_detectada": apolice_detectada,
        "estipulante_detectado": estipulante_detectado,
        "mes_aniversario_detectado": mes_aniv_det,
        "evidencias_aniversario": evidencias,
        "inicio_vigencia_detectado": inicio_vig_det,
        "erros": erros,
        "grupos": grupos,
    }


# ─────────────────────── Auto-detecção ───────────────────────

def detectar_mes_aniversario(
    grupos: dict,
    pct_min: float = 0.03,
    pct_max: float = 0.40,
) -> tuple[int | None, list[str]]:
    """Detecta o mês de aniversário olhando saltos de valor por beneficiário.

    Reajuste anual contratual típico: 3% a 40% (range inclui anos extremos
    como 2022 com 25%+ pós-pandemia). Saltos por faixa etária são geralmente
    maiores e em mês fora do aniversário — esses são ignorados pela contagem
    majoritária.

    Retorna `(mes, evidencias)` — se ambíguo retorna `(None, [...])`.
    """
    from collections import Counter
    saltos_por_mes: Counter[int] = Counter()
    evidencias: list[str] = []
    for chave, g in grupos.items():
        cobr = sorted(g["cobrancas"], key=lambda c: c["competencia"])
        # Pega valor base de cada competência (uma vez)
        vals: dict[str, float] = {}
        for c in cobr:
            vals.setdefault(c["competencia"], float(c["valor"]))
        items = sorted(vals.items())
        for i in range(1, len(items)):
            comp_ant, v_ant = items[i - 1]
            comp_atu, v_atu = items[i]
            if v_ant <= 0:
                continue
            pct = (v_atu / v_ant) - 1
            if pct_min <= pct <= pct_max:
                mes = int(comp_atu.split("-")[1])
                saltos_por_mes[mes] += 1
                evidencias.append(
                    f"{g['nome']}: {comp_ant} R${v_ant:.2f} → {comp_atu} R${v_atu:.2f} ({pct*100:+.1f}%)"
                )

    if not saltos_por_mes:
        return None, evidencias
    # Mês majoritário precisa ter ao menos 50% dos saltos detectados
    total = sum(saltos_por_mes.values())
    mes, n = saltos_por_mes.most_common(1)[0]
    if n / total >= 0.5:
        return mes, evidencias
    return None, evidencias


def detectar_inicio_vigencia(grupos: dict) -> date | None:
    """Pega a MAIS ANTIGA `data_inicio_vigencia` entre os beneficiários.

    Em planos coletivos o titular geralmente entrou primeiro. Dependentes
    podem ter datas posteriores — não usamos.
    """
    datas: list[date] = []
    for g in grupos.values():
        d = g.get("data_inicio_vigencia")
        if isinstance(d, date):
            datas.append(d)
    return min(datas) if datas else None
