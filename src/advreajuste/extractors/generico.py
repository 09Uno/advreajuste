"""Extrator GENÉRICO — funciona em QUALQUER demonstrativo tabular.

Estratégia: análise por linha de texto procurando o padrão universal de
demonstrativo de plano de saúde brasileiro:

    [matrícula] NOME [CPF] [data nasc] [idade] [parentesco] [data vig] R$ VALOR

Não exige formato específico. Funciona em qualquer operadora desde que o PDF
contenha uma linha por beneficiário com nome + valor monetário.

Coberturas testadas:
  ✓ Sul América (Saúde OnLine, Relatório Faturamento)
  ✓ Bradesco Saúde (formato típico)
  ✓ Amil (formato típico)
  ✓ Notre Dame / Hapvida (lista simples por vida)
  ✓ Unimed (formatos cooperativa)
  ✓ Boletos individuais (1 vida só)

Fallback puro Python — sem dependência de LLM/API/internet.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import pdfplumber
from loguru import logger


# ─────────────────────── Padrões ───────────────────────

# CPF: aceita 11 dígitos crus ou formatado xxx.xxx.xxx-xx
RE_CPF = re.compile(r"\b(\d{3}\.?\d{3}\.?\d{3}-?\d{2}|\d{11})\b")

# Valor monetário em R$ — exige vírgula decimal e 2 casas
RE_VALOR = re.compile(r"R?\$?\s*((?:\d{1,3}\.)*\d{1,3},\d{2})\b")

# Data DD/MM/YYYY
RE_DATA = re.compile(r"\b(\d{2}/\d{2}/\d{4})\b")

# Grau de parentesco
RE_PAREN = re.compile(
    r"\b(TITULAR|C[ÔO]NJUGE|FILHOS?|FILHA[S]?|AGREGADO[S]?|DEPENDENTE[S]?|ESPOSA?O?)\b",
    re.I,
)

# Nome em CAIXA ALTA — 2+ palavras de letras maiúsculas
RE_NOME_CAPS = re.compile(
    r"([A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-ZÁÉÍÓÚÂÊÔÃÕÇ ]{3,60}[A-ZÁÉÍÓÚÂÊÔÃÕÇ])"
)

# Mês de competência: cobre vários formatos
RE_COMP_PERIODO = re.compile(
    r"Per[ií]odo\s+de\s+Compet[eê]ncia[^\d]*(\d{2})/(\d{2})/(\d{4})", re.I,
)
RE_COMP_REF = re.compile(
    r"(?:M[eê]s|Compet[eê]ncia|Refer[eê]ncia|Refer[eê]nte\s+a|Mensalidade\s+Referente\s+a)"
    r"[:\s]+"
    r"(?P<mes>0?[1-9]|1[0-2])[/-](?P<ano>20\d{2})",
    re.I,
)
RE_COMP_TEXTO_MES = re.compile(
    r"\b(jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)[a-z]*[/\s.-]+"
    r"(20\d{2}|\d{2})\b",
    re.I,
)
MESES_NOME = {
    "jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
    "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12,
}

# Linhas a ignorar (totais, impostos, headers etc.)
RE_IGNORAR = re.compile(
    r"\b(?:total[\s:.]|subtotal|tot\.|iof\b|coparticipa[çc][ãa]o|"
    r"^total\s+geral|valor\s+total|valor\s+geral|"
    r"total\s+da\s+fam[ií]lia|total\s+geral|"
    r"fechamento\b|vencimento\b|imposto\b|d[ée]bito\s+autom|"
    r"dda\b|boleto\b|c[óo]digo\s+de\s+barras|nfse?\b|cnpj\b|"
    r"valor\s+da\s+us|premio\s+da\s+us|"
    r"periodo\s+de\s+compet|m[eê]s\s+refer|"
    r"raz[ãa]o\s+social|estipulante|empregador|contratante|"
    r"dados\s+da\s+empresa|listagem\s+de|resumo|"
    r"benefici[áa]rios?:|vidas\s+cobertas|"
    r"endere[çc]o|inscri[çc][ãa]o)",
    re.I,
)

# Palavras-marcador a remover do FIM do nome (quando o regex de caixa
# alta consome erroneamente sufixos como "CPF", "DN", "NASC" etc.)
SUFIXOS_NOME = {
    "CPF", "NASC", "NASCIMENTO", "DN", "IDADE", "RG", "DATA",
    "MATRICULA", "MATR", "CARTAO", "GRAU", "PARENTESCO",
    "INICIO", "VIGENCIA", "INCLUSAO", "ADESAO", "PLANO",
    "MENSALIDADE", "PREMIO", "VALOR", "TOTAL",
}

# Operadoras conhecidas — regex tolerante a encoding ruim (caracteres acentuados
# vêm como `�` em alguns PDFs com fonte Symbol/Type1 sem ToUnicode CMap).
OPERADORAS = [
    ("Sul América", r"sul\s*am.?rica"),
    ("Bradesco", r"bradesco\s+sa.?de"),
    ("Amil", r"\bamil\b"),
    ("Unimed", r"\bunimed\b"),
    ("Hapvida", r"\bhapvida\b"),
    ("Notre Dame", r"notre\s+dame"),
    ("Care Plus", r"care\s+plus"),
    ("Porto Seguro", r"porto\s+seguro\s+sa.?de"),
    ("Allianz", r"allianz\s+sa.?de"),
    ("Cassi", r"\bcassi\b"),
    ("GEAP", r"\bgeap\b"),
    ("Saúde Caixa", r"sa.?de\s+caixa"),
    ("Mediservice", r"mediservice"),
    ("Omint", r"\bomint\b"),
    ("São Cristóvão", r"s.?o\s+crist.?v.?o"),
]

# Padrões para apólice / estipulante
RE_APOLICE = re.compile(r"Ap[óo]lice[\s:Nn°.º]*([A-Z0-9\-./]{4,30})", re.I)
RE_ESTIP = re.compile(
    r"(?:Raz[ãa]o\s+Social|Estipulante|Empregador|Contratante)[\s:]+"
    r"([A-ZÁÉÍÓÚÂÊÔÃÕÇ][^\n]{4,80})",
    re.I,
)


# ─────────────────────── Estrutura ───────────────────────

@dataclass(frozen=True)
class LinhaGenerica:
    competencia: str
    nome: str
    cpf: str
    data_nascimento: date | None
    parentesco: str | None
    valor: Decimal
    data_inicio_vigencia: date | None
    origem_pdf: str
    pagina: int


# ─────────────────────── Helpers ───────────────────────

def _normaliza_cpf(s: str) -> str:
    return re.sub(r"\D", "", s)


def _parse_brl(s: str) -> Decimal:
    return Decimal(s.replace(".", "").replace(",", "."))


def _parse_data_br(s: str) -> date | None:
    try:
        d, m, y = (int(x) for x in s.split("/"))
        return date(y, m, d)
    except Exception:
        return None


def _detectar_competencia(texto: str) -> str | None:
    """Acha mês de competência no texto da página."""
    m = RE_COMP_PERIODO.search(texto)
    if m:
        _, mes, ano = m.groups()
        return f"{int(ano):04d}-{int(mes):02d}"
    m = RE_COMP_REF.search(texto)
    if m:
        mes = int(m.group("mes"))
        ano = int(m.group("ano"))
        if ano < 100:
            ano += 2000
        return f"{ano:04d}-{mes:02d}"
    m = RE_COMP_TEXTO_MES.search(texto)
    if m:
        nome_mes = m.group(1)[:3].lower()
        ano = int(m.group(2))
        if ano < 100:
            ano += 2000
        mes = MESES_NOME.get(nome_mes)
        if mes:
            return f"{ano:04d}-{mes:02d}"
    return None


def _detectar_operadora(texto: str) -> str | None:
    for nome, pat in OPERADORAS:
        if re.search(pat, texto, re.I):
            return nome
    # Heurísticas por formato característico (operadora não aparece em texto)
    if re.search(r"relat[óo]rio\s+de\s+faturamento\s+de\s+segurados", texto, re.I):
        return "Sul América"  # formato exclusivo Sul América Relatório PME
    if re.search(r"sa[úu]de\s+onl?ine", texto, re.I):
        return "Sul América"  # produto Sul América Saúde OnLine
    return None


def _extrair_apolice_estip(texto: str) -> tuple[str | None, str | None]:
    apolice = None
    estip = None
    m = RE_APOLICE.search(texto)
    if m:
        apolice = m.group(1).rstrip(".,;:")
    m = RE_ESTIP.search(texto)
    if m:
        estip = m.group(1).strip().rstrip(".,;:").rstrip()
    return apolice, estip


def _parse_linha_beneficiario(linha: str) -> dict | None:
    """Tenta extrair {nome, cpf, valor, ...} de uma linha de tabela.

    Retorna None se a linha não parecer um beneficiário.
    """
    if not linha or len(linha.strip()) < 15:
        return None
    if RE_IGNORAR.search(linha):
        return None

    # 1. Precisa ter um valor BRL
    valores = list(RE_VALOR.finditer(linha))
    if not valores:
        return None
    valor_match = valores[-1]  # último valor é o prêmio individual
    valor = _parse_brl(valor_match.group(1))
    if valor < Decimal("30") or valor > Decimal("50000"):
        return None

    # 2. Procura nome (maiúsculas)
    # Pega o primeiro match dentro da linha (excluindo headers)
    nome_match = None
    for m in RE_NOME_CAPS.finditer(linha):
        candidato = m.group(1).strip()
        # Filtra "RAZÃO SOCIAL", "DADOS DA EMPRESA" etc.
        partes = candidato.split()
        if len(partes) < 2:
            continue
        # Exclui se for nome de operadora ou termos técnicos
        if any(p in candidato.upper() for p in ("RAZAO", "RAZÃO", "DADOS", "EMPRESA",
                                                  "CNPJ", "INSCR", "ENDEREÇO", "ENDERECO")):
            continue
        nome_match = m
        break
    if not nome_match:
        return None
    nome = nome_match.group(1).strip()
    # Remove sufixos-marcador (ex: "RICARDO PEREIRA SILVA      CPF" → tira "CPF")
    palavras = nome.split()
    while palavras and palavras[-1].upper() in SUFIXOS_NOME:
        palavras.pop()
    if len(palavras) < 2:
        return None
    nome = " ".join(palavras).title()

    # 3. CPF (opcional)
    cpf = None
    cpf_match = RE_CPF.search(linha)
    if cpf_match:
        cpf = _normaliza_cpf(cpf_match.group(1))
        if len(cpf) != 11:
            cpf = None

    # 4. Parentesco
    par_match = RE_PAREN.search(linha)
    parentesco = None
    if par_match:
        parentesco = par_match.group(1).upper().replace("Ô", "O")
        # Normaliza variantes
        if parentesco.startswith("FILH"):
            parentesco = "FILHO"
        elif parentesco.startswith("ESPOS"):
            parentesco = "CONJUGE"
        elif parentesco == "CONJUGE" or parentesco == "CÔNJUGE":
            parentesco = "CONJUGE"
        elif parentesco.startswith("AGREG"):
            parentesco = "AGREGADO"
        elif parentesco.startswith("DEPEND"):
            parentesco = "DEPENDENTE"

    # 5. Datas — tipicamente 2 (DN + vigência)
    datas_str = RE_DATA.findall(linha)
    datas = [_parse_data_br(d) for d in datas_str]
    datas = [d for d in datas if d is not None]
    data_nasc = datas[0] if datas else None
    data_vig = datas[1] if len(datas) >= 2 else None

    return {
        "nome": nome,
        "cpf": cpf,
        "parentesco": parentesco,
        "valor": valor,
        "data_nascimento": data_nasc,
        "data_inicio_vigencia": data_vig,
    }


def _parse_multi_linha_individual(texto: str) -> dict | None:
    """Fallback para faturas individuais (1 vida) onde o nome, parentesco e
    valor estão em linhas separadas.

    Estratégia: se encontrar UM nome + UM CPF + UM valor BRL no texto da
    página inteira (e nada mais), trata como 1 beneficiário.
    """
    # Procura mensalidade
    valor_matches = list(RE_VALOR.finditer(texto))
    valores_validos = [v for v in valor_matches
                        if Decimal("30") <= _parse_brl(v.group(1)) <= Decimal("50000")]
    if len(valores_validos) != 1:
        return None  # Não é caso individual
    valor = _parse_brl(valores_validos[0].group(1))

    # Procura nome - primeira sequência de 2+ palavras em caixa alta
    nome = None
    for m in RE_NOME_CAPS.finditer(texto):
        candidato = m.group(1).strip()
        partes = candidato.split()
        if len(partes) < 2:
            continue
        # Filtra termos não-pessoais
        if any(p in candidato.upper() for p in (
                "RAZAO", "RAZÃO", "DADOS", "EMPRESA", "CNPJ", "INSCR",
                "ENDERECO", "ENDEREÇO", "PLANO", "APOLICE", "APÓLICE",
                "SAUDE", "SAÚDE", "INTERMEDICA", "INTERMÉDICA",
                "ASSISTENCIA", "ASSISTÊNCIA", "MEDICA", "MÉDICA",
                "OPERADORA", "ESTIPULANTE", "CONTRATO", "COMPETENCIA",
                "COMPETÊNCIA", "VIGENCIA", "VIGÊNCIA", "INDIVIDUAL",
                "FAMILIAR", "EMPRESARIAL", "ADESAO", "ADESÃO")):
            continue
        # Remove sufixos
        while partes and partes[-1].upper() in SUFIXOS_NOME:
            partes.pop()
        if len(partes) >= 2:
            nome = " ".join(partes).title()
            break

    if not nome:
        return None

    # Procura CPF
    cpf_match = RE_CPF.search(texto)
    cpf = _normaliza_cpf(cpf_match.group(1)) if cpf_match else None
    if cpf and len(cpf) != 11:
        cpf = None

    # Procura parentesco e datas no texto todo
    par_match = RE_PAREN.search(texto)
    parentesco = "TITULAR"
    if par_match:
        p = par_match.group(1).upper().replace("Ô", "O")
        if p.startswith("FILH"): parentesco = "FILHO"
        elif p.startswith("AGREG"): parentesco = "AGREGADO"
        elif p.startswith("DEPEND"): parentesco = "DEPENDENTE"
        elif p.startswith(("CONJUGE", "CÔNJUGE", "ESPOS")): parentesco = "CONJUGE"
        else: parentesco = p

    datas_str = RE_DATA.findall(texto)
    datas = [_parse_data_br(d) for d in datas_str if _parse_data_br(d)]
    data_nasc = datas[0] if datas else None
    data_vig = datas[1] if len(datas) >= 2 else None

    return {
        "nome": nome,
        "cpf": cpf,
        "parentesco": parentesco,
        "valor": valor,
        "data_nascimento": data_nasc,
        "data_inicio_vigencia": data_vig,
    }


# ─────────────────────── API pública ───────────────────────

def extrair_pdf(pdf_path: Path) -> tuple[list[LinhaGenerica], dict]:
    """Extrai beneficiários de UM PDF + metadados do cabeçalho.

    Retorna `(linhas, meta)` onde meta = {operadora, apolice, estipulante}.
    """
    linhas: list[LinhaGenerica] = []
    operadora = None
    apolice = None
    estipulante = None

    with pdfplumber.open(pdf_path) as pdf:
        # Concatena todo texto pra detectar metadados globais
        texto_full = ""
        for page in pdf.pages:
            texto_full += "\n" + (page.extract_text() or "")

        operadora = _detectar_operadora(texto_full)
        apolice, estipulante = _extrair_apolice_estip(texto_full)

        # Para cada página, detecta competência e extrai linhas
        for page_num, page in enumerate(pdf.pages, start=1):
            texto = page.extract_text() or ""
            competencia = _detectar_competencia(texto)
            if not competencia:
                # Tenta usar competência detectada no documento todo
                competencia = _detectar_competencia(texto_full)
            if not competencia:
                continue

            linhas_pagina = []
            for linha_txt in texto.split("\n"):
                resultado = _parse_linha_beneficiario(linha_txt)
                if not resultado:
                    continue
                linhas_pagina.append(LinhaGenerica(
                    competencia=competencia,
                    nome=resultado["nome"],
                    cpf=resultado["cpf"] or "",
                    data_nascimento=resultado["data_nascimento"],
                    parentesco=resultado["parentesco"],
                    valor=resultado["valor"],
                    data_inicio_vigencia=resultado["data_inicio_vigencia"],
                    origem_pdf=pdf_path.name,
                    pagina=page_num,
                ))

            # Se nenhuma linha — tenta fallback multi-linha (faturas individuais)
            if not linhas_pagina:
                resultado = _parse_multi_linha_individual(texto)
                if resultado:
                    linhas_pagina.append(LinhaGenerica(
                        competencia=competencia,
                        nome=resultado["nome"],
                        cpf=resultado["cpf"] or "",
                        data_nascimento=resultado["data_nascimento"],
                        parentesco=resultado["parentesco"],
                        valor=resultado["valor"],
                        data_inicio_vigencia=resultado["data_inicio_vigencia"],
                        origem_pdf=pdf_path.name,
                        pagina=page_num,
                    ))

            linhas.extend(linhas_pagina)

    # Dedup por (competência, cpf|nome)
    vistos: set[tuple[str, str]] = set()
    unicas: list[LinhaGenerica] = []
    for l in linhas:
        chave = (l.competencia, l.cpf or l.nome.upper())
        if chave in vistos:
            continue
        vistos.add(chave)
        unicas.append(l)

    return unicas, {
        "operadora": operadora,
        "apolice": apolice,
        "estipulante": estipulante,
    }


def extrair_pasta(pdfs: list[Path], progress_cb=None) -> dict:
    """Extrai todos PDFs e devolve dict no shape esperado pelo pipeline."""
    todas: list[LinhaGenerica] = []
    operadoras_det: list[str] = []
    apolices_det: list[str] = []
    estipulantes_det: list[str] = []
    erros: list[str] = []

    for i, pdf in enumerate(pdfs, start=1):
        if progress_cb:
            try:
                progress_cb(i, len(pdfs), pdf.name)
            except Exception:
                pass
        try:
            linhas, meta = extrair_pdf(pdf)
            if not linhas:
                erros.append(f"{pdf.name}: 0 cobranças extraídas")
            todas.extend(linhas)
            if meta["operadora"]:
                operadoras_det.append(meta["operadora"])
            if meta["apolice"]:
                apolices_det.append(meta["apolice"])
            if meta["estipulante"]:
                estipulantes_det.append(meta["estipulante"])
        except Exception as e:
            logger.error("Falha em {}: {}", pdf.name, e)
            erros.append(f"{pdf.name}: {e}")

    # Reconcilia CPFs faltantes via nome → CPF (de outros PDFs)
    nome_to_cpf: dict[str, str] = {}
    for l in todas:
        if l.cpf:
            nome_to_cpf.setdefault(l.nome.upper(), l.cpf)
    todas_recon: list[LinhaGenerica] = []
    for l in todas:
        if l.cpf:
            todas_recon.append(l)
        else:
            cpf = nome_to_cpf.get(l.nome.upper(), "")
            todas_recon.append(LinhaGenerica(
                competencia=l.competencia, nome=l.nome, cpf=cpf,
                data_nascimento=l.data_nascimento, parentesco=l.parentesco,
                valor=l.valor, data_inicio_vigencia=l.data_inicio_vigencia,
                origem_pdf=l.origem_pdf, pagina=l.pagina,
            ))
    todas = todas_recon

    # Agrupa por (cpf ou nome)
    grupos: dict[str, dict] = {}
    for l in todas:
        chave = l.cpf or l.nome.upper()
        if chave not in grupos:
            grupos[chave] = {
                "nome": l.nome,
                "cpf": l.cpf,
                "data_nascimento": l.data_nascimento or date(1970, 1, 1),
                "data_inicio_vigencia": l.data_inicio_vigencia,
                "parentesco": l.parentesco or "TITULAR",
                "cobrancas": [],
            }
        if not grupos[chave]["cpf"] and l.cpf:
            grupos[chave]["cpf"] = l.cpf
        if not grupos[chave].get("data_inicio_vigencia") and l.data_inicio_vigencia:
            grupos[chave]["data_inicio_vigencia"] = l.data_inicio_vigencia
        grupos[chave]["cobrancas"].append({
            "competencia": l.competencia,
            "valor": l.valor,
            "nome": l.nome,
            "cpf": l.cpf,
            "parentesco": l.parentesco or "TITULAR",
            "origem_pdf": l.origem_pdf,
            "data_nascimento": grupos[chave]["data_nascimento"],
            "data_inicio_vigencia": l.data_inicio_vigencia,
            "pagina": l.pagina,
        })

    from collections import Counter
    op_pred = Counter(operadoras_det).most_common(1)[0][0] if operadoras_det else None
    ap_pred = Counter(apolices_det).most_common(1)[0][0] if apolices_det else None
    estip_pred = Counter(estipulantes_det).most_common(1)[0][0] if estipulantes_det else None

    # Detecção de aniversário e vigência
    from .universal import detectar_mes_aniversario, detectar_inicio_vigencia
    mes_aniv, evidencias = detectar_mes_aniversario(grupos)
    inicio_vig = detectar_inicio_vigencia(grupos)

    return {
        "tipo": "generico_tabular",
        "n_pdfs": len(pdfs),
        "n_linhas": len(todas),
        "n_beneficiarios": len(grupos),
        "operadora_detectada": op_pred,
        "apolice_detectada": ap_pred,
        "estipulante_detectado": estip_pred,
        "mes_aniversario_detectado": mes_aniv,
        "evidencias_aniversario": evidencias,
        "inicio_vigencia_detectado": inicio_vig,
        "erros": erros,
        "grupos": grupos,
    }
