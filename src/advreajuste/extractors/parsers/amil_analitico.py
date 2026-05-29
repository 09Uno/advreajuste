"""Parser Amil 'Demonstrativo Analítico de Faturamento'.

Formato observado em casos coletivos empresariais Amil (ex: SOS Turismo).
Cada PÁGINA é um mês, header indica 'Mensalidade - MM/AAAA', e tem uma
linha por beneficiário com:

  N° (9 dígitos) NOME CPF (11 dígitos) PLANO_CODIGOS Tp.Id IDADE
  [DEPENDENCIA] DATA_INCLUSAO RUBRICA VALOR_INDIVIDUAL [VALOR_TOTAL_FAMILIA]

Onde Tp.Id é T (Titular) ou D (Dependente).
"""
from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import pdfplumber

from .. import text_extraction


RE_HEADER = re.compile(
    r"demonstrativo\s+anal[íi]tico\s+de\s+faturamento", re.I
)
RE_OPERADORA_AMIL = re.compile(r"operadora\s*:?\s*amil", re.I)
RE_COMPETENCIA_AMIL = re.compile(
    r"Mensalidade\s*-?\s*(?P<mes>0?[1-9]|1[0-2])/(?P<ano>20\d{2})", re.I
)

# Linha de beneficiário:
#   086014299 LUIZ MARCELO DE CARVALHO PINTO 29029433850 AMIL ONE S1500 QP NAC R2 PJ T 41
#   03/05/2023 Mens. Titular Faixa Etária Implant. 1.826,19 4.330,37
RE_LINHA_BENEF = re.compile(
    r"^(?P<num>\d{6,12})\s+"
    r"(?P<nome>[A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-ZÁÉÍÓÚÂÊÔÃÕÇ ]{4,60}?)\s+"
    r"(?P<cpf>\d{11})\s+"
    r".+?\s+"
    r"(?P<tipo>[TDA])\s+"
    r"(?P<idade>\d{1,3})\s+"
    r"(?:(?P<dependencia>Conjuge|Filho/Filha|Pai|M[ãa]e|Esposa?o?|Outro|Companheir[oa])\s+)?"
    r"(?P<data>\d{2}/\d{2}/\d{4})\s+"
    r".+?\s+"
    r"R?\$?\s*(?P<valor>\d{1,3}(?:\.\d{3})*,\d{2})"
    r"(?:\s+(?P<vtotal>\d{1,3}(?:\.\d{3})*,\d{2}))?\s*$",
)

# Linhas a ignorar
RE_IGNORAR_AMIL = re.compile(
    r"\b(?:total\s+contrato|total\s+de\s+benefici[áa]rios|total\s+geral|"
    r"subtotal|mens\.|desconto|d[ée]bitos|cr[ée]ditos)\b",
    re.I,
)


def _parse_brl(s: str) -> Decimal:
    return Decimal(s.replace(".", "").replace(",", "."))


def _parse_data_br(s: str):
    from datetime import date
    try:
        d, m, y = (int(x) for x in s.split("/"))
        return date(y, m, d)
    except Exception:
        return None


def detectar(pdf_path: Path) -> bool:
    paginas, _ = text_extraction.extrair_paginas(pdf_path, permitir_ocr=False)
    if not paginas:
        return False
    txt = paginas[0]
    return bool(RE_HEADER.search(txt) and RE_OPERADORA_AMIL.search(txt))


def extrair_pdf(pdf_path: Path) -> list[dict]:
    cobrancas: list[dict] = []
    paginas, _ = text_extraction.extrair_paginas(pdf_path, permitir_ocr=False)
    for page_num, txt in enumerate(paginas, start=1):
        if not txt.strip():
            continue
            m_comp = RE_COMPETENCIA_AMIL.search(txt)
            if not m_comp:
                continue
            competencia = f"{int(m_comp.group('ano')):04d}-{int(m_comp.group('mes')):02d}"

            for linha in txt.split("\n"):
                linha_str = linha.strip()
                if RE_IGNORAR_AMIL.search(linha_str):
                    continue
                m = RE_LINHA_BENEF.match(linha_str)
                if not m:
                    continue
                tipo = m.group("tipo").upper()
                dependencia = m.group("dependencia")
                if tipo == "T":
                    parentesco = "TITULAR"
                elif tipo == "D":
                    if dependencia and "Conjuge" in dependencia:
                        parentesco = "CONJUGE"
                    elif dependencia and "Filh" in dependencia:
                        parentesco = "FILHO"
                    else:
                        parentesco = "DEPENDENTE"
                else:
                    parentesco = "AGREGADO"

                cobrancas.append({
                    "nome": m.group("nome").strip().title(),
                    "cpf": m.group("cpf"),
                    "parentesco": parentesco,
                    "competencia": competencia,
                    "valor": _parse_brl(m.group("valor")),
                    "data_nascimento": None,
                    "data_inicio_vigencia": _parse_data_br(m.group("data")),
                    "pagina": page_num,
                    "origem_pdf": pdf_path.name,
                })

    return cobrancas
