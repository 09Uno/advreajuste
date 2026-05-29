"""Parser Sul América PME / Saúde OnLine — extração DINÂMICA (não hardcoded).

Dois layouts:
- Tipo 1 (Saúde OnLine, 2009–2015): `MATR(15) PLANO(4) CR(2)NOME DD/MM/YYYY IDADE PARENTESCO DD/MM/YYYY US PREMIO PREMIO_VIDA`
- Tipo 2 (Relatório faturamento, 2016+): `MATR(17) NOME CPF(10-11) PLANO FUNCIONAL DD/MM/YYYY IDADE PARENTESCO DD/MM/YYYY R$ VALOR`
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import pdfplumber


@dataclass(frozen=True)
class LinhaPagamento:
    competencia: str  # YYYY-MM
    nome: str
    cpf: str  # pode vir vazio no tipo 1
    data_nascimento: date
    parentesco: str
    valor: Decimal
    origem_pdf: str
    pagina: int
    data_inicio_vigencia: date | None = None  # quando o beneficiário entrou


RE_COMP = re.compile(
    r"Per[ií]odo\s+de\s+Compet[eê]ncia[^0-9]*(\d{2})/(\d{2})/(\d{4})", re.I
)

# Tipo 2 — 2016+. CPF pode ter 10 ou 11 dígitos.
# Há sub-formatos com 1 ou 2 campos entre CPF e DN:
#   - "Sul América Saúde OnLine PME": MATR NOME CPF PLANO FUNC DN IDADE PAR VIG R$VAL
#   - "Relatório de Faturamento": MATR NOME CPF FUNC DN IDADE PAR VIG R$VAL
#     (plano vem em linha separada, ex: "37764-ESPECIAL")
RE_TIPO2 = re.compile(
    r"(?P<matr>\d{15,19})\s+"
    r"(?P<nome>[A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-ZÁÉÍÓÚÂÊÔÃÕÇ ]{4,60}?)\s+"
    r"(?P<cpf>\d{10,11})\s+"
    r"(?:\S+\s+)?"                                            # plano (opcional)
    r"\d+\s+"                                                  # ID funcional
    r"(?P<dn>\d{2}/\d{2}/\d{4})\s+"
    r"\d+\s+"
    r"(?P<par>TITULAR|CONJUGE|C[ÔO]NJUGE|FILHOS?|AGREGADO|DEPENDENTE)\s+"
    r"(?P<vig>\d{2}/\d{2}/\d{4})\s+"
    r"R?\$?\s*(?P<val>[\d.,]+)"
)

# Tipo 1 — Saúde OnLine (2009-2020). 3 variações de formato:
# (a) 2009-12:  "32394001001 8252 99 ELISANGELA GRIGIO LIRIO 22/07/1974 35 TITULAR ..."
# (b) 2013-17:  "239432394001001 8252 99ELISANGELA GRIGIO LIRIO 22/07/1974 41 TITULAR ..."
# (c) 2018-20:  "23943239400200126072 99LUIZ ANTONIO GRIGIO 06/03/1949 71 TITULAR ..."
#               (matrícula+plano grudados, CR grudado ao nome)
RE_TIPO1 = re.compile(
    r"(?P<matr>\d{10,25})"                                   # matr [+plano grudado]
    r"(?:\s+\d{3,5})?\s*"                                     # plano opcional c/ espaço
    r"\d{1,2}\s*"                                             # CR (pode colar no nome)
    r"(?P<nome>[A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-ZÁÉÍÓÚÂÊÔÃÕÇ ]{4,60}?)\s+"
    r"(?P<dn>\d{2}/\d{2}/\d{4})\s+"
    r"\d+\s+"
    r"(?P<par>TITULAR|CONJUGE|C[ÔO]NJUGE|FILHOS?|AGREGADO|DEPENDENTE)\s+"
    r"(?P<vig>\d{2}/\d{2}/\d{4})\s+"
    r"[\d.,]+\s+"
    r"[\d.,]+\s+"
    r"(?P<val>[\d.,]+)"
)


def _parse_brl(s: str) -> Decimal:
    s = s.strip().replace(".", "").replace(",", ".")
    return Decimal(s)


def _parse_data(s: str) -> date:
    d, m, y = (int(x) for x in s.split("/"))
    return date(y, m, d)


def _pad_cpf(s: str) -> str:
    s = re.sub(r"\D", "", s)
    if len(s) == 10:
        s = "0" + s
    return s


def _extrair_bloco(texto: str, competencia: str, pdf_name: str, pagina: int) -> list[LinhaPagamento]:
    linhas: list[LinhaPagamento] = []
    dedup: set[tuple[str, str]] = set()

    for m in RE_TIPO2.finditer(texto):
        cpf = _pad_cpf(m.group("cpf"))
        key = (competencia, cpf)
        if key in dedup:
            continue
        dedup.add(key)
        try:
            vig = _parse_data(m.group("vig"))
        except Exception:
            vig = None
        linhas.append(
            LinhaPagamento(
                competencia=competencia,
                nome=m.group("nome").strip().title(),
                cpf=cpf,
                data_nascimento=_parse_data(m.group("dn")),
                parentesco=m.group("par").upper().replace("Ô", "O").replace("C ONJUGE", "CONJUGE"),
                valor=_parse_brl(m.group("val")),
                origem_pdf=pdf_name,
                pagina=pagina,
                data_inicio_vigencia=vig,
            )
        )

    if linhas:
        return linhas

    # Fallback tipo 1 (sem CPF)
    for m in RE_TIPO1.finditer(texto):
        nome = m.group("nome").strip().title()
        key = (competencia, nome.upper())
        if key in dedup:
            continue
        dedup.add(key)
        try:
            vig = _parse_data(m.group("vig"))
        except Exception:
            vig = None
        linhas.append(
            LinhaPagamento(
                competencia=competencia,
                nome=nome,
                cpf="",  # será reconciliado depois via nome
                data_nascimento=_parse_data(m.group("dn")),
                parentesco=m.group("par").upper().replace("Ô", "O"),
                valor=_parse_brl(m.group("val")),
                origem_pdf=pdf_name,
                pagina=pagina,
                data_inicio_vigencia=vig,
            )
        )
    return linhas


def extrair_pdf(pdf_path: Path) -> list[LinhaPagamento]:
    """Concatena TODO o texto do PDF e divide por 'Período de Competência'.

    Isso garante que blocos que atravessam páginas (muito comum no formato
    Saúde OnLine 2009-2015, que empilha 3 competências por página) sejam
    extraídos integralmente.
    """
    linhas: list[LinhaPagamento] = []
    partes: list[tuple[int, str]] = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            partes.append((i, page.extract_text() or ""))

    texto_full = ""
    # Mapear posição no texto agregado → página de origem (aproximado)
    pos_pagina: list[tuple[int, int]] = []  # (pos_inicio, pagina)
    for i, t in partes:
        pos_pagina.append((len(texto_full), i))
        texto_full += "\n" + t

    def pagina_em(pos: int) -> int:
        pg = 1
        for start, p in pos_pagina:
            if pos >= start:
                pg = p
            else:
                break
        return pg

    matches = list(RE_COMP.finditer(texto_full))
    if not matches:
        return []
    for k, m in enumerate(matches):
        _, mes, ano = m.groups()
        comp = f"{int(ano):04d}-{int(mes):02d}"
        ini = m.end()
        fim = matches[k + 1].start() if k + 1 < len(matches) else len(texto_full)
        linhas.extend(_extrair_bloco(
            texto_full[ini:fim], comp, pdf_path.name, pagina_em(ini)
        ))
    return linhas


def extrair_pasta(pdfs: list[Path]) -> list[LinhaPagamento]:
    out: list[LinhaPagamento] = []
    for p in pdfs:
        out.extend(extrair_pdf(p))
    return out


def reconciliar_cpfs(linhas: list[LinhaPagamento]) -> list[LinhaPagamento]:
    """Preenche CPFs faltantes (tipo 1) usando nome→CPF dos tipos 2."""
    nome_to_cpf: dict[str, str] = {}
    for l in linhas:
        if l.cpf and l.nome.upper() not in nome_to_cpf:
            nome_to_cpf[l.nome.upper()] = l.cpf

    # Também aceitar nomes com pequenas variações ("LIRA CELIA ANDRDAE" vs "ANDRADE")
    def match(nome: str) -> str:
        u = nome.upper()
        if u in nome_to_cpf:
            return nome_to_cpf[u]
        for k, v in nome_to_cpf.items():
            if _similaridade(u, k) >= 0.85:
                return v
        return ""

    saida: list[LinhaPagamento] = []
    for l in linhas:
        if l.cpf:
            saida.append(l)
        else:
            cpf = match(l.nome)
            saida.append(
                LinhaPagamento(
                    competencia=l.competencia, nome=l.nome, cpf=cpf,
                    data_nascimento=l.data_nascimento, parentesco=l.parentesco,
                    valor=l.valor, origem_pdf=l.origem_pdf, pagina=l.pagina,
                    data_inicio_vigencia=l.data_inicio_vigencia,
                )
            )
    return saida


def _similaridade(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    sa, sb = set(a.split()), set(b.split())
    inter = sa & sb
    return len(inter) / max(len(sa), len(sb))


def agrupar_beneficiarios(linhas: list[LinhaPagamento]) -> dict[str, dict]:
    """Retorna {cpf_ou_nome_norm: {nome, cpf, data_nascimento, parentesco,
    data_inicio_vigencia, cobrancas: [...] }}"""
    grupos: dict[str, dict] = {}
    for l in linhas:
        chave = l.cpf or l.nome.upper()
        g = grupos.setdefault(chave, {
            "nome": l.nome, "cpf": l.cpf, "data_nascimento": l.data_nascimento,
            "parentesco": l.parentesco, "cobrancas": [],
            "data_inicio_vigencia": l.data_inicio_vigencia,
        })
        # manter cpf se foi descoberto em qualquer linha
        if not g["cpf"] and l.cpf:
            g["cpf"] = l.cpf
        # propagar inicio vigência (só atualiza se ainda não tem)
        if not g.get("data_inicio_vigencia") and l.data_inicio_vigencia:
            g["data_inicio_vigencia"] = l.data_inicio_vigencia
        g["cobrancas"].append(l)
    return grupos
