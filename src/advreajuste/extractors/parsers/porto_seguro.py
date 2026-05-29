"""Parser Porto Seguro Saúde / Mediservice.

Formato observado:
- Página 1: 'RESUMO DA FATURA' com totalizadores (sem vidas)
- Página 2+: 'RELAÇÃO ATUALIZADA DE SEGURADOS' com linha por beneficiário
  e blocos de TOTAL DO DEP. (Prêmio + Inscrição + IOF).

Linha do beneficiário:
  CONTRATO N NOME IDADE PARENTESCO PLANO Inc\\ DATA I Prêmio Base VALOR

Depois aparecem linhas auxiliares "Inscrição de Lotação", "IOF" e
"TOTAL DO DEP. VALOR_TOTAL" que somam o real pago.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pdfplumber

from .. import text_extraction


RE_HEADER_RESUMO = re.compile(r"resumo\s+da\s+fatura", re.I)
RE_HEADER_RELACAO = re.compile(r"rela[çc][ãa]o\s+atualizada\s+de\s+segurados", re.I)

RE_COMPETENCIA = re.compile(r"refer[eê]ncia\s*:?\s*(?P<mes>0?[1-9]|1[0-2])/(?P<ano>20\d{2})", re.I)
RE_APOLICE = re.compile(r"ap[óo]lice[\s:]+(\d+)", re.I)
RE_ESTIPULANTE = re.compile(r"estipulante[\s:]+\d+\s*-\s*(.+?)\s+(?:sucursal|p[áa]gina)", re.I)

# Linha do beneficiário (texto bruto após extract_text)
# Primeiro beneficiário da página: "46376703 1 EDIVALDO ... Prêmio Base 1.225,60"
# Demais: "2 LIGIA MARCIA SOARES DA SILVA 59 Conjuge ... Prêmio Base 2.267,25"
# (Seguro só aparece no 1º — depois só o Dep)
RE_LINHA_BENEF = re.compile(
    r"^(?:(?P<segn>\d{6,12})\s+)?"
    r"(?P<dep>\d{1,3})\s+"
    r"(?P<nome>[A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-ZÁÉÍÓÚÂÊÔÃÕÇ ]{4,60}?)\s+"
    r"(?P<idade>\d{1,3})\s+"
    r"(?P<parentesco>Titular|C[ôo]njuge|Filh[oa]|Agregado|Dependente|Companheir[oa])\s+"
    r".*?"
    r"(?:(?P<data>\d{2}/\d{2}/\d{4})\s+.*?)?"
    r"Pr[êe]mio\s+Base\s+"
    r"(?P<valor>\d{1,3}(?:\.\d{3})*,\d{2})",
    re.I,
)

# Linha de TOTAL DO DEP (valor real pago após IOF + taxas)
RE_TOTAL_DEP = re.compile(r"TOTAL\s+DO\s+DEP\.?\s+(\d{1,3}(?:\.\d{3})*,\d{2})", re.I)


def _parse_brl(s: str) -> Decimal:
    return Decimal(s.replace(".", "").replace(",", "."))


def _parse_data_br(s: str):
    try:
        d, m, y = (int(x) for x in s.split("/"))
        return date(y, m, d)
    except Exception:
        return None


def detectar(pdf_path: Path) -> bool:
    paginas, _ = text_extraction.extrair_paginas(pdf_path, permitir_ocr=False)
    if not paginas:
        return False
    return bool(RE_HEADER_RESUMO.search(paginas[0]))


def extrair_pdf(pdf_path: Path) -> list[dict]:
    """Extrai beneficiários percorrendo páginas 'RELAÇÃO ATUALIZADA DE SEGURADOS'."""
    cobrancas: list[dict] = []
    paginas = text_extraction.extrair_paginas(pdf_path, permitir_ocr=False)[0]
    # Cada página pode ter sua própria competência (PDFs multi-mês)
    for page_num, txt in enumerate(paginas, start=1):
            if not RE_HEADER_RELACAO.search(txt):
                continue
            m_comp = RE_COMPETENCIA.search(txt)
            if not m_comp:
                continue
            competencia = f"{int(m_comp.group('ano')):04d}-{int(m_comp.group('mes')):02d}"

            # Estratégia: a cada linha que casa RE_LINHA_BENEF, captura o
            # beneficiário; depois quando encontrar "TOTAL DO DEP. VALOR"
            # usa esse valor (mais preciso que Prêmio Base sozinho).
            benef_pendente: dict | None = None
            for linha in txt.split("\n"):
                linha = linha.strip()

                m = RE_LINHA_BENEF.match(linha)
                if m:
                    # Fecha beneficiário anterior se tiver
                    if benef_pendente:
                        cobrancas.append(benef_pendente)
                    parentesco_raw = m.group("parentesco").upper().replace("Ô", "O")
                    if parentesco_raw.startswith("FILH"):
                        parentesco = "FILHO"
                    elif parentesco_raw.startswith(("CONJ", "CÔNJ")):
                        parentesco = "CONJUGE"
                    elif parentesco_raw.startswith("AGREG"):
                        parentesco = "AGREGADO"
                    elif parentesco_raw.startswith("DEPEND"):
                        parentesco = "DEPENDENTE"
                    else:
                        parentesco = "TITULAR"
                    benef_pendente = {
                        "nome": m.group("nome").strip().title(),
                        "cpf": "",
                        "parentesco": parentesco,
                        "competencia": competencia,
                        "valor": _parse_brl(m.group("valor")),  # Prêmio Base
                        "data_nascimento": None,
                        "data_inicio_vigencia": _parse_data_br(m.group("data")),
                        "pagina": page_num,
                        "origem_pdf": pdf_path.name,
                    }
                    continue

                # Atualiza valor com TOTAL DO DEP. (Prêmio + Inscrição + IOF)
                m_tot = RE_TOTAL_DEP.search(linha)
                if m_tot and benef_pendente:
                    benef_pendente["valor"] = _parse_brl(m_tot.group(1))

            if benef_pendente:
                cobrancas.append(benef_pendente)
                benef_pendente = None

    return cobrancas


def extrair_metadados(pdf_path: Path) -> dict:
    """Apólice + estipulante."""
    try:
        paginas, _ = text_extraction.extrair_paginas(pdf_path, permitir_ocr=False)
        txt = paginas[0] if paginas else ""
    except Exception:
        return {}
    apolice = None
    estipulante = None
    m = RE_APOLICE.search(txt or "")
    if m:
        apolice = m.group(1)
    m = RE_ESTIPULANTE.search(txt or "")
    if m:
        estipulante = m.group(1).strip()
    return {"apolice": apolice, "estipulante": estipulante,
            "operadora": "Porto Seguro"}
