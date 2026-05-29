"""Router de extração — tenta cada parser específico em ordem, com fallback
no extrator genérico.

Ordem (do mais específico ao mais genérico):
1. Sul América PME (regex sulamerica_pme.py)
2. Qualicorp (Recibos Mensais ou Consolidado Anual)
3. Amil Demonstrativo Analítico
4. Porto Seguro / Mediservice
5. Cassi BEN120
6. Genérico tabular
7. (futuro) OCR fallback

Todos 100% offline. Zero chamadas a API.
"""
from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from decimal import Decimal
import os
from pathlib import Path

from loguru import logger

from .parsers import (
    amil_analitico,
    cassi,
    porto_seguro,
    qualicorp,
    sulamerica_pme,
)
from . import generico
from . import text_extraction


def _normalizar_grupos(cobrancas: list[dict]) -> dict[str, dict]:
    """Agrupa cobranças por (cpf ou nome) → estrutura de `grupos`."""
    grupos: dict[str, dict] = {}
    for c in cobrancas:
        chave = c.get("cpf") or c["nome"].upper()
        if chave not in grupos:
            grupos[chave] = {
                "nome": c["nome"],
                "cpf": c.get("cpf") or "",
                "data_nascimento": c.get("data_nascimento") or date(1970, 1, 1),
                "data_inicio_vigencia": c.get("data_inicio_vigencia"),
                "parentesco": c.get("parentesco") or "TITULAR",
                "cobrancas": [],
            }
        if not grupos[chave]["cpf"] and c.get("cpf"):
            grupos[chave]["cpf"] = c["cpf"]
        if not grupos[chave].get("data_inicio_vigencia") and c.get("data_inicio_vigencia"):
            grupos[chave]["data_inicio_vigencia"] = c["data_inicio_vigencia"]
        grupos[chave]["cobrancas"].append({
            "competencia": c["competencia"],
            "valor": c["valor"],
            "nome": c["nome"],
            "cpf": c.get("cpf") or "",
            "parentesco": c.get("parentesco") or "TITULAR",
            "origem_pdf": c.get("origem_pdf", ""),
            "data_nascimento": grupos[chave]["data_nascimento"],
            "data_inicio_vigencia": c.get("data_inicio_vigencia"),
            "pagina": c.get("pagina", 0),
        })
    return grupos


def _detectar_parser(pdf: Path) -> str | None:
    """Identifica qual parser usar pra um PDF específico."""
    # 1. Qualicorp (administradora — usado por Bradesco/Amil/Sul América via adesão)
    fmt_qc = qualicorp.detectar_formato(pdf)
    if fmt_qc:
        return f"qualicorp_{fmt_qc}"

    # 2. Amil Analítico
    if amil_analitico.detectar(pdf):
        return "amil_analitico"

    # 3. Porto Seguro / Mediservice
    if porto_seguro.detectar(pdf):
        return "porto_seguro"

    # 4. Cassi BEN120
    if cassi.detectar(pdf):
        return "cassi"

    # 5. Sul América PME (formato Saúde OnLine ou Relatório Faturamento)
    try:
        linhas_sap = sulamerica_pme.extrair_pdf(pdf)
        if linhas_sap:
            return "sulamerica_pme"
    except Exception:
        pass

    return None


def _processar_via_parser(
    pdf: Path,
    parser: str,
    operadoras_det: list,
    apolices_det: list,
    estipulantes_det: list,
    tipos_plano_det: list | None = None,
) -> list[dict]:
    """Roda um parser específico em um PDF e retorna as cobranças.
    Atualiza listas de metadados in-place."""
    if parser.startswith("qualicorp_"):
        fmt = parser.split("_", 1)[1]
        if fmt == "mensal":
            cobrancas = qualicorp.extrair_mensal(pdf)
        else:
            cobrancas = qualicorp.extrair_anual(pdf)
        op = qualicorp.detectar_operadora_qualicorp(pdf)
        if op:
            operadoras_det.append(op)
        return cobrancas

    if parser == "amil_analitico":
        operadoras_det.append("Amil")
        return amil_analitico.extrair_pdf(pdf)

    if parser == "porto_seguro":
        cobrancas = porto_seguro.extrair_pdf(pdf)
        meta = porto_seguro.extrair_metadados(pdf)
        if meta.get("operadora"):
            operadoras_det.append(meta["operadora"])
        if meta.get("apolice"):
            apolices_det.append(meta["apolice"])
        if meta.get("estipulante"):
            estipulantes_det.append(meta["estipulante"])
        return cobrancas

    if parser == "cassi":
        meta = cassi.extrair_metadados(pdf)
        operadoras_det.append(meta.get("operadora") or "Cassi")
        if meta.get("apolice"):
            apolices_det.append(meta["apolice"])
        if meta.get("estipulante"):
            estipulantes_det.append(meta["estipulante"])
        if tipos_plano_det is not None and meta.get("tipo_plano"):
            tipos_plano_det.append(meta["tipo_plano"])
        return cassi.extrair_pdf(pdf)

    if parser == "sulamerica_pme":
        linhas_dc = sulamerica_pme.reconciliar_cpfs(
            sulamerica_pme.extrair_pdf(pdf)
        )
        cobrancas = [{
            "nome": l.nome, "cpf": l.cpf, "parentesco": l.parentesco,
            "competencia": l.competencia, "valor": l.valor,
            "data_nascimento": l.data_nascimento,
            "data_inicio_vigencia": l.data_inicio_vigencia,
            "pagina": l.pagina, "origem_pdf": l.origem_pdf,
        } for l in linhas_dc]
        operadoras_det.append("Sul América")
        # Apólice + estipulante do header
        import pdfplumber, re as _re
        try:
            paginas, _ = text_extraction.extrair_paginas(pdf, permitir_ocr=False)
            txt = paginas[0] if paginas else ""
            m = _re.search(r"Ap[óo]lice[:\s]+(\S+)", txt, _re.I)
            if m:
                apolices_det.append(m.group(1).strip().rstrip(".:"))
            m = _re.search(r"Raz[ãa]o\s+Social[:\s]+(.+?)(?:\n|$)", txt, _re.I)
            if m:
                estipulantes_det.append(m.group(1).strip())
        except Exception:
            pass
        return cobrancas

    return []


def _max_workers_pdf() -> int:
    try:
        return max(1, min(8, int(os.environ.get("ADVREAJUSTE_PDF_WORKERS", "2"))))
    except ValueError:
        return 2


def _max_workers_ocr() -> int:
    try:
        return max(1, min(2, int(os.environ.get("ADVREAJUSTE_OCR_WORKERS", "1"))))
    except ValueError:
        return 1


def _chunks(items: list[Path], size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _processar_pdf_inicial(pdf: Path) -> dict:
    """Processa um PDF textual isoladamente para permitir paralelismo."""
    if text_extraction.eh_escaneado(pdf):
        return {"pdf": pdf, "escaneado": True}

    parser = _detectar_parser(pdf)
    if parser is None:
        return {"pdf": pdf, "sem_parser": True}

    operadoras: list[str] = []
    apolices: list[str] = []
    estipulantes: list[str] = []
    tipos_plano: list[str] = []
    try:
        cobrancas = _processar_via_parser(
            pdf, parser, operadoras, apolices, estipulantes, tipos_plano,
        )
        erro = f"{pdf.name}: 0 cobranças via {parser}" if not cobrancas else None
        return {
            "pdf": pdf,
            "parser": parser,
            "cobrancas": cobrancas,
            "operadoras": operadoras,
            "apolices": apolices,
            "estipulantes": estipulantes,
            "tipos_plano": tipos_plano,
            "erro": erro,
        }
    except Exception as e:
        logger.error("Falha em {} ({}): {}", pdf.name, parser, e)
        return {"pdf": pdf, "parser": parser, "erro": f"{pdf.name}: {e}"}


def extrair_pasta(
    pdfs: list[Path],
    progress_cb=None,
    permitir_ocr: bool = True,
) -> dict:
    """Roteia cada PDF para seu parser ideal e consolida resultados."""
    todas_cobrancas: list[dict] = []
    parsers_usados: Counter[str] = Counter()
    operadoras_det: list[str] = []
    apolices_det: list[str] = []
    estipulantes_det: list[str] = []
    tipos_plano_det: list[str] = []
    erros: list[str] = []
    pdfs_sem_parser: list[Path] = []
    pdfs_escaneados: list[Path] = []

    completed = 0
    workers = _max_workers_pdf()
    for lote in _chunks(pdfs, workers * 10):
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_processar_pdf_inicial, pdf): pdf for pdf in lote}
            for future in as_completed(futures):
                res = future.result()
                completed += 1
                pdf = res["pdf"]
                if progress_cb:
                    try:
                        progress_cb(completed, len(pdfs), pdf.name)
                    except Exception:
                        pass

                if res.get("escaneado"):
                    pdfs_escaneados.append(pdf)
                    continue
                if res.get("sem_parser"):
                    pdfs_sem_parser.append(pdf)
                    continue

                parser = res.get("parser")
                if parser:
                    parsers_usados[parser] += 1
                todas_cobrancas.extend(res.get("cobrancas") or [])
                operadoras_det.extend(res.get("operadoras") or [])
                apolices_det.extend(res.get("apolices") or [])
                estipulantes_det.extend(res.get("estipulantes") or [])
                tipos_plano_det.extend(res.get("tipos_plano") or [])
                if res.get("erro"):
                    erros.append(res["erro"])

    # PDFs escaneados — tenta OCR se disponível
    if pdfs_escaneados and permitir_ocr and text_extraction._ocr_disponivel():
        ocr_workers = _max_workers_ocr()
        logger.info(
            f"{len(pdfs_escaneados)} PDF(s) escaneados — aplicando OCR com {ocr_workers} worker(s)"
        )
        parsers_usados["ocr"] = parsers_usados.get("ocr", 0) + len(pdfs_escaneados)

        def _ocr_progress(i, total, nome):
            if progress_cb:
                try:
                    progress_cb(len(pdfs) - len(pdfs_escaneados) + i,
                                len(pdfs), f"OCR: {nome}")
                except Exception:
                    pass

        text_extraction.ocr_em_paralelo(
            pdfs_escaneados, max_workers=ocr_workers, progress_cb=_ocr_progress,
        )

        # Agora processa cada um (sequencial — leve, lê do cache)
        for pdf_esc in pdfs_escaneados:
            try:
                parser = _detectar_parser(pdf_esc)
                if parser:
                    cobrancas_pdf = _processar_via_parser(
                        pdf_esc, parser,
                        operadoras_det, apolices_det, estipulantes_det,
                    )
                    if cobrancas_pdf:
                        parsers_usados[f"{parser}_ocr"] = parsers_usados.get(f"{parser}_ocr", 0) + 1
                        todas_cobrancas.extend(cobrancas_pdf)
                    else:
                        erros.append(f"{pdf_esc.name}: OCR ok mas {parser} extraiu 0")
                else:
                    pdfs_sem_parser.append(pdf_esc)
            except Exception as e:
                logger.error("Processamento pos-OCR falhou em {}: {}", pdf_esc.name, e)
                erros.append(f"{pdf_esc.name}: {e}")
    elif pdfs_escaneados:
        # Sem OCR disponível — reporta como erro amigável
        for p in pdfs_escaneados:
            erros.append(f"{p.name}: PDF escaneado (precisa OCR — Tesseract não disponível)")

    # PDFs sem parser específico → tenta genérico (texto extraível) em lote
    if pdfs_sem_parser:
        logger.info(f"{len(pdfs_sem_parser)} PDF(s) sem parser específico — tentando genérico tabular")
        ex_gen = generico.extrair_pasta(pdfs_sem_parser)
        # Adiciona linhas do genérico em formato dict
        for k, g in ex_gen["grupos"].items():
            for c in g["cobrancas"]:
                todas_cobrancas.append({
                    "nome": g["nome"],
                    "cpf": g["cpf"] or "",
                    "parentesco": g.get("parentesco") or "TITULAR",
                    "competencia": c["competencia"],
                    "valor": c["valor"],
                    "data_nascimento": g.get("data_nascimento"),
                    "data_inicio_vigencia": g.get("data_inicio_vigencia"),
                    "pagina": c.get("pagina", 0),
                    "origem_pdf": c.get("origem_pdf", ""),
                })
        if ex_gen["n_linhas"] > 0:
            parsers_usados["generico"] += len(pdfs_sem_parser)
            if ex_gen.get("operadora_detectada"):
                operadoras_det.append(ex_gen["operadora_detectada"])
            if ex_gen.get("apolice_detectada"):
                apolices_det.append(ex_gen["apolice_detectada"])
            if ex_gen.get("estipulante_detectado"):
                estipulantes_det.append(ex_gen["estipulante_detectado"])
        # PDFs sem extração em nenhum método
        if ex_gen["n_linhas"] == 0:
            for p in pdfs_sem_parser:
                erros.append(f"{p.name}: formato não reconhecido (texto vazio ou layout desconhecido)")

    # Reconcilia CPFs faltantes via nome → CPF (cross-match)
    nome_to_cpf: dict[str, str] = {}
    for c in todas_cobrancas:
        if c.get("cpf"):
            nome_to_cpf.setdefault(c["nome"].upper(), c["cpf"])
    for c in todas_cobrancas:
        if not c.get("cpf") and c["nome"].upper() in nome_to_cpf:
            c["cpf"] = nome_to_cpf[c["nome"].upper()]

    # Agrupa
    grupos = _normalizar_grupos(todas_cobrancas)

    # Metadados predominantes
    op_pred = Counter(operadoras_det).most_common(1)[0][0] if operadoras_det else None
    ap_pred = Counter(apolices_det).most_common(1)[0][0] if apolices_det else None
    estip_pred = Counter(estipulantes_det).most_common(1)[0][0] if estipulantes_det else None
    tipo_plano_pred = Counter(tipos_plano_det).most_common(1)[0][0] if tipos_plano_det else None

    # Auto-detecção
    from .universal import detectar_mes_aniversario, detectar_inicio_vigencia
    mes_aniv, evidencias = detectar_mes_aniversario(grupos)
    inicio_vig = detectar_inicio_vigencia(grupos)

    return {
        "tipo": "router_multi_parser",
        "parsers_usados": dict(parsers_usados),
        "n_pdfs": len(pdfs),
        "n_linhas": len(todas_cobrancas),
        "n_beneficiarios": len(grupos),
        "operadora_detectada": op_pred,
        "apolice_detectada": ap_pred,
        "estipulante_detectado": estip_pred,
        "tipo_plano_detectado": tipo_plano_pred,
        "mes_aniversario_detectado": mes_aniv,
        "evidencias_aniversario": evidencias,
        "inicio_vigencia_detectado": inicio_vig,
        "erros": erros,
        "grupos": grupos,
    }
