"""Parser Cassi — BEN120 Demonstrativo de Pagamento de Faturas.

Formato observado:
- 1 PDF = 1 vida (titular)
- Cabeçalho com Nome, CPF, Contrato, Data de Adesão
- Tabela linha por mês:
  Competência Vencimento Data_Baixa Valor Tipo_Lançamento Forma_Pagamento

PDFs Cassi têm `(cid:XX)` no texto (fonte custom sem ToUnicode CMap).
Removemos antes de parsear.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pdfplumber

from .. import text_extraction


RE_BEN120 = re.compile(r"BEN120|CASSI", re.I)
RE_CID = re.compile(r"\(cid:\d+\)")

RE_NOME = re.compile(r"Nome\s*:\s*([^\n]+?)\s*$", re.M)
RE_CPF = re.compile(r"CPF\s*:\s*(\d{11})")
RE_CONTRATO = re.compile(r"Contrato\s*:\s*(.+?)\s*$", re.M)
RE_ADESAO = re.compile(r"Data\s+de\s+Ades[ãa]o\s*:\s*(\d{2}/\d{2}/\d{4})")
RE_MATRICULA = re.compile(r"Matr[íi]cula\s+Cassi\s*:\s*(\S+)")

# Linha: "11/2018 29/11/2018 29/11/2018 1.514,92 Mensalidade ..."
RE_LINHA = re.compile(
    r"^(?P<mes>0?[1-9]|1[0-2])/(?P<ano>20\d{2})\s+"
    r"\d{2}/\d{2}/\d{4}\s+"
    r"\d{2}/\d{2}/\d{4}\s+"
    r"(?P<valor>\d{1,3}(?:\.\d{3})*,\d{2})\s+"
    r"(?P<tipo>Mensalidade|Acerto|Multa|Juros|Coparticipa[çc][ãa]o)",
    re.I,
)


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
    txt_clean = RE_CID.sub("", paginas[0])
    return bool(RE_BEN120.search(txt_clean))


def extrair_metadados(pdf_path: Path) -> dict:
    """Retorna {operadora, apolice (matrícula), estipulante (contrato), tipo_plano}."""
    txt_full = text_extraction.texto_completo(pdf_path, permitir_ocr=False)
    txt_clean = RE_CID.sub("", txt_full)
    m_matr = RE_MATRICULA.search(txt_clean)
    m_contrato = RE_CONTRATO.search(txt_clean)
    return {
        "operadora": "Cassi",
        "apolice": m_matr.group(1).strip() if m_matr else None,
        "estipulante": (m_contrato.group(1).strip() if m_contrato else None),
        # Cassi Família II e similares são planos coletivos por adesão
        "tipo_plano": "coletivo_adesao",
    }


def extrair_pdf(pdf_path: Path) -> list[dict]:
    txt_full = text_extraction.texto_completo(pdf_path, permitir_ocr=False)
    txt_clean = RE_CID.sub("", txt_full)

    # Cabeçalho
    m_nome = RE_NOME.search(txt_clean)
    m_cpf = RE_CPF.search(txt_clean)
    m_ades = RE_ADESAO.search(txt_clean)

    if not m_nome:
        return []
    nome = m_nome.group(1).strip().title()
    cpf = m_cpf.group(1) if m_cpf else ""
    data_adesao = _parse_data_br(m_ades.group(1)) if m_ades else None

    cobrancas: list[dict] = []
    for linha in txt_clean.split("\n"):
        linha = linha.strip()
        if not linha:
            continue
        m = RE_LINHA.match(linha)
        if not m:
            continue
        # Só interessa "Mensalidade" pra cálculo de reajuste
        if m.group("tipo").lower() != "mensalidade":
            continue
        ano = int(m.group("ano"))
        mes = int(m.group("mes"))
        cobrancas.append({
            "nome": nome,
            "cpf": cpf,
            "parentesco": "TITULAR",
            "competencia": f"{ano:04d}-{mes:02d}",
            "valor": _parse_brl(m.group("valor")),
            "data_nascimento": None,
            "data_inicio_vigencia": data_adesao,
            "pagina": 1,
            "origem_pdf": pdf_path.name,
        })

    return cobrancas
