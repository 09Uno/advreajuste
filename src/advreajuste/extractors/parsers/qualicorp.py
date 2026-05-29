"""Parser Qualicorp — administradora de benefícios coletiva (planos de
adesão Sul América, Amil, Bradesco etc.).

Há DOIS sub-formatos comuns nos demonstrativos:

1. **Recibos Mensais** — 1 PÁGINA por mês, listando cada cobertura
   contratada por beneficiário com valor parcial. Soma-se por (nome, mês)
   pra obter mensalidade total de cada vida.

2. **Consolidado Anual** — 1 PDF por ano, com bloco mensal (12 linhas)
   contendo VALOR TOTAL DA FAMÍLIA + bloco "Composição do Grupo Familiar"
   com valores ANUAIS por beneficiário. Divide-se proporcionalmente pra
   reconstruir mensalidades por vida.

Cobertura observada nos casos reais:
  - Bradesco Mayara Borges (Mensal)
  - Amil Alvaro Braghetta (Anual)
  - Sul América Adriana Correia (Anual)
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pdfplumber
from loguru import logger

from .. import text_extraction


# ─────────────────────── Detecção ───────────────────────

RE_QUALICORP = re.compile(r"qualicorp", re.I)
RE_RECIBO = re.compile(r"recibo\s+n", re.I)
RE_DEMO_ANUAL = re.compile(r"demonstrativo\s+de\s+pagamentos?\s*[-—]?\s*(20\d{2})", re.I)


def detectar_formato(pdf_path: Path) -> str | None:
    """Retorna 'mensal', 'anual' ou None se não for Qualicorp.

    Usa text_extraction (que tem cache + OCR fallback), então funciona
    mesmo em PDFs escaneados desde que OCR já tenha rodado.
    """
    paginas, _ = text_extraction.extrair_paginas(pdf_path, permitir_ocr=False)
    if not paginas:
        return None
    txt = paginas[0]

    if not RE_QUALICORP.search(txt):
        return None
    if RE_RECIBO.search(txt):
        return "mensal"
    if RE_DEMO_ANUAL.search(txt):
        return "anual"
    return None


# ─────────────────────── Parser Mensal ───────────────────────

# Linha: "NOME COMPLETO BENEFICIO MM/AAAA R$ VALOR"
# - NOME pode ter "FILHO"/"FILHA" como sufixo (dependente)
# - BENEFICIO: BRADESCO, AMIL, SUL AMERICA, etc.
# - Tolerante a OCR: aceita letras minúsculas no início do nome
#   (OCR às vezes troca J por j, A por a etc.)
RE_LINHA_RECIBO = re.compile(
    r"^(?P<nome>[A-Za-zÁÉÍÓÚÂÊÔÃÕÇáéíóúâêôãõç][A-Za-zÁÉÍÓÚÂÊÔÃÕÇáéíóúâêôãõç ]+?)\s+"
    r"(?P<beneficio>BRADESCO|AMIL|SUL\s*AM[ÉE]RICA|UNIMED|HAPVIDA|"
    r"NOTRE\s*DAME|CARE\s*PLUS|PORTO\s*SEGURO|ALLIANZ|CASSI|GEAP|"
    r"MEDISERVICE|OMINT)\s+"
    r"(?P<mes>0?[1-9]|1[0-2])/(?P<ano>20\d{2})\s+"
    r"R?\$?\s*(?P<valor>[\d.,]+)\s*$",
    re.I,
)

RE_RECIBO_VALOR_TOTAL = re.compile(r"importância\s+de\s+R\$?\s*([\d.,]+)", re.I)


def _parse_brl(s: str) -> Decimal:
    return Decimal(s.replace(".", "").replace(",", "."))


def extrair_mensal(pdf_path: Path) -> list[dict]:
    """Extrai recibos mensais Qualicorp.

    Cada vida pode aparecer em N linhas (várias coberturas) — soma por
    (nome_normalizado, competencia) pra retornar valor único por vida-mês.
    """
    cobrancas_brutas: list[dict] = []
    paginas, _ = text_extraction.extrair_paginas(pdf_path, permitir_ocr=False)

    for page_num, txt in enumerate(paginas, start=1):
        if not txt.strip() or "qualicorp" not in txt.lower():
            continue
        for linha in txt.split("\n"):
                m = RE_LINHA_RECIBO.match(linha.strip())
                if not m:
                    continue
                nome = m.group("nome").strip().title()
                # Normaliza "Filho"/"Filha" como sufixo
                parentesco = "TITULAR"
                if re.search(r"\s+FILH[OA]S?\s*$", m.group("nome"), re.I):
                    parentesco = "FILHO"
                comp = f"{int(m.group('ano')):04d}-{int(m.group('mes')):02d}"
                valor = _parse_brl(m.group("valor"))
                cobrancas_brutas.append({
                    "nome": nome,
                    "parentesco": parentesco,
                    "competencia": comp,
                    "valor": valor,
                    "pagina": page_num,
                    "origem_pdf": pdf_path.name,
                })

    # Agrega: soma valores por (nome, competencia)
    agregado: dict[tuple[str, str], dict] = {}
    for c in cobrancas_brutas:
        chave = (c["nome"].upper(), c["competencia"])
        if chave not in agregado:
            agregado[chave] = {
                "nome": c["nome"],
                "parentesco": c["parentesco"],
                "competencia": c["competencia"],
                "valor": Decimal("0"),
                "pagina": c["pagina"],
                "origem_pdf": c["origem_pdf"],
                "cpf": None,
                "data_nascimento": None,
                "data_inicio_vigencia": None,
            }
        agregado[chave]["valor"] += c["valor"]

    return list(agregado.values())


# ─────────────────────── Parser Anual ───────────────────────

MESES_PT_NUM = {
    "janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3,
    "abril": 4, "maio": 5, "junho": 6, "julho": 7,
    "agosto": 8, "setembro": 9, "outubro": 10,
    "novembro": 11, "dezembro": 12,
}

RE_LINHA_MENSAL_ANUAL = re.compile(
    r"^(janeiro|fevereiro|mar[çc]o|abril|maio|junho|julho|"
    r"agosto|setembro|outubro|novembro|dezembro)\s+"
    r"R?\$?\s*([\d.,]+)\s*$",
    re.I,
)

RE_LINHA_COMPOSICAO = re.compile(
    r"^(?P<cond>Titular|C[ôo]njuge|Filho\(a\)|Filha?\(?o?\)?|Dependente|Agregado)\s+"
    r"(?P<nome>[A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-ZÁÉÍÓÚÂÊÔÃÕÇ ]{4,80}?)\s+"
    r"(?P<cpf>\d{3}\.?\d{3}\.?\d{3}-?\d{2})\s+"
    r"(?P<valor>[\d.,]+)\s*$",
    re.I,
)


def extrair_anual(pdf_path: Path) -> list[dict]:
    """Extrai demonstrativo Qualicorp anual (12 meses + composição familiar).

    Estratégia: usa proporção do valor anual por beneficiário pra dividir
    o valor mensal da família.
    """
    txt_full = text_extraction.texto_completo(pdf_path, permitir_ocr=False)

    # Detecta ano
    m_ano = RE_DEMO_ANUAL.search(txt_full)
    if not m_ano:
        return []
    ano = int(m_ano.group(1))

    # Detecta valores mensais da família
    valores_mensais: dict[int, Decimal] = {}
    for linha in txt_full.split("\n"):
        m = RE_LINHA_MENSAL_ANUAL.match(linha.strip())
        if m:
            mes_nome = m.group(1).lower().replace("ç", "c")
            mes = MESES_PT_NUM.get(mes_nome)
            if mes:
                valores_mensais[mes] = _parse_brl(m.group(2))

    if not valores_mensais:
        return []

    # Detecta composição familiar
    composicao: list[dict] = []
    for linha in txt_full.split("\n"):
        m = RE_LINHA_COMPOSICAO.match(linha.strip())
        if not m:
            continue
        cond = m.group("cond").upper()
        if cond.startswith("FILH"):
            parentesco = "FILHO"
        elif cond.startswith("CON") or cond.startswith("CÔN"):
            parentesco = "CONJUGE"
        elif cond.startswith("AGREG"):
            parentesco = "AGREGADO"
        elif cond.startswith("DEPEND"):
            parentesco = "DEPENDENTE"
        else:
            parentesco = "TITULAR"
        composicao.append({
            "nome": m.group("nome").strip().title(),
            "cpf": re.sub(r"\D", "", m.group("cpf")),
            "parentesco": parentesco,
            "valor_anual": _parse_brl(m.group("valor")),
        })

    if not composicao:
        return []

    # Soma valores anuais → proporção por beneficiário
    total_anual = sum(c["valor_anual"] for c in composicao)
    if total_anual <= 0:
        return []

    # Gera 1 linha por (beneficiário × mês), distribuindo valor mensal por proporção
    cobrancas: list[dict] = []
    for mes, valor_familia in sorted(valores_mensais.items()):
        for b in composicao:
            proporcao = b["valor_anual"] / total_anual
            valor_mes_benef = (valor_familia * proporcao).quantize(Decimal("0.01"))
            cobrancas.append({
                "nome": b["nome"],
                "cpf": b["cpf"],
                "parentesco": b["parentesco"],
                "competencia": f"{ano:04d}-{mes:02d}",
                "valor": valor_mes_benef,
                "data_nascimento": None,
                "data_inicio_vigencia": None,
                "pagina": 1,
                "origem_pdf": pdf_path.name,
            })

    return cobrancas


# ─────────────────────── API unificada ───────────────────────

def extrair_pdf(pdf_path: Path) -> list[dict]:
    """Detecta o sub-formato e extrai."""
    fmt = detectar_formato(pdf_path)
    if fmt == "mensal":
        return extrair_mensal(pdf_path)
    if fmt == "anual":
        return extrair_anual(pdf_path)
    return []


def detectar_operadora_qualicorp(pdf_path: Path) -> str | None:
    """O Qualicorp é a administradora; a operadora real fica numa frase.
    Ex: 'firmado com o(a) AMIL ASSISTENCIA MEDICA INTERNACIONAL SA' → 'Amil'.
    """
    txt = text_extraction.texto_completo(pdf_path, permitir_ocr=False)
    if not txt:
        return None

    mapeamento = [
        (r"sul\s*am[ée]rica", "Sul América"),
        (r"\bbradesco\b", "Bradesco"),
        (r"\bamil\b", "Amil"),
        (r"\bunimed\b", "Unimed"),
        (r"\bhapvida\b", "Hapvida"),
        (r"notre\s+dame", "Notre Dame"),
        (r"care\s+plus", "Care Plus"),
        (r"porto\s+seguro", "Porto Seguro"),
        (r"allianz", "Allianz"),
        (r"mediservice", "Mediservice"),
    ]
    for pat, nome in mapeamento:
        if re.search(pat, txt, re.I):
            return nome
    return None
