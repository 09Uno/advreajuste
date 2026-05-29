"""Pipeline end-to-end — metodologia de SUBSTITUIÇÃO como padrão.

Orquestra: extração (parsers + Claude) → motor de substituição (reajuste
observado vs teto ANS) → correção monetária multi-índice → planilha openpyxl
formato pericial → minuta DOCX → audit log JSONL.
"""
from __future__ import annotations

import json
import time
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Literal

from loguru import logger

from .calculators.correcao_monetaria import (
    atualizar, juros_1pct_am, pacote_completo, TipoIndice,
)
from .calculators.substituicao import (
    LinhaSubstituicao, motor_substituicao, totalizar_substituicao,
)
from .config import settings
from .custody import registrar_evento, registrar_original
from .extractors import Beneficiario, Caso, Contrato, ParcelaCobrada
from .extractors.hybrid import extrair as extrair_hibrido
from .writers.peticao import gerar_peticao
from .writers.xlsx_template import gerar_planilha_template


def _caso_dir(caso_id: str) -> Path:
    d = settings.casos_dir / caso_id
    d.mkdir(parents=True, exist_ok=True)
    return d


# ───────────────────────── EXTRAÇÃO ─────────────────────────

def ingerir_pdfs(
    caso_id: str,
    pasta_pdfs: Path,
    usar_vision: bool = False,
    parser_operadora: str | None = "universal",
    cpfs_esperados: list[str] | None = None,
    max_pct_faltantes_para_vision: float = 0.10,
    progress_cb=None,
) -> list[dict]:
    """Extrai todos PDFs da pasta.

    Modos de `parser_operadora`:
    - `"universal"` (default): Gemini Vision em qualquer operadora (~R$ 0,15/caso)
    - `"sulamerica_pme"`: regex específico Sul América PME (rápido e grátis,
      mas só para demonstrativo empresarial multi-competência)
    - `None` ou `"hibrido"`: cascata template → vision por boleto

    Se `usar_vision=True` e regex cobre menos de (1 - max_pct_faltantes), aciona
    Gemini 2.5 Flash como fallback para extrair dados estruturados.
    """
    from .extractors.parsers import sulamerica_pme
    from .extractors.vision_fallback import extrair_com_fallback
    from .extractors import universal as universal_ext

    resultados: list[dict] = []

    if parser_operadora == "universal":
        pdfs = sorted(Path(pasta_pdfs).glob("*.pdf"))
        for p in pdfs:
            registrar_original(p, caso_id)
        if not pdfs:
            resultados.append({"tipo": "router_vazio", "n_pdfs": 0,
                               "n_linhas": 0, "n_beneficiarios": 0, "grupos": {}})
            return resultados

        # ROTEAMENTO via router_extracao — todas as camadas offline:
        #   1. Detecta escaneados precoce (pula parsers se sem texto)
        #   2. Tenta parser específico por operadora (Qualicorp, Amil
        #      Analítico, Porto Seguro, Cassi, Sul América PME)
        #   3. Cai no extrator genérico tabular se nenhum parser específico
        #   4. OCR (Tesseract) se PDF escaneado e tesseract disponível
        from .extractors import router_extracao

        extr = router_extracao.extrair_pasta(
            pdfs, progress_cb=progress_cb, permitir_ocr=True,
        )
        registrar_evento(caso_id, "extracao_router",
                         {"n_pdfs": extr["n_pdfs"], "n_linhas": extr["n_linhas"],
                          "n_beneficiarios": extr["n_beneficiarios"],
                          "operadora": extr.get("operadora_detectada"),
                          "parsers": extr.get("parsers_usados", {})})
        resultados.append(extr)
        return resultados

    if parser_operadora == "sulamerica_pme":
        pdfs = sorted(Path(pasta_pdfs).glob("*.pdf"))
        for p in pdfs:
            registrar_original(p, caso_id)
        linhas_regex = sulamerica_pme.reconciliar_cpfs(sulamerica_pme.extrair_pasta(pdfs))

        # Vision fallback apenas se necessário
        if usar_vision and cpfs_esperados:
            linhas = extrair_com_fallback(
                pdfs, linhas_regex, cpfs_esperados=cpfs_esperados,
                max_pct_faltantes=max_pct_faltantes_para_vision,
            )
            linhas = sulamerica_pme.reconciliar_cpfs(linhas)
        else:
            linhas = linhas_regex

        grupos = sulamerica_pme.agrupar_beneficiarios(linhas)

        # Diagnóstico: se zero extrações, captura prévia dos PDFs pra debug
        preview_pdfs = []
        if not grupos and pdfs:
            try:
                import pdfplumber
                for p in pdfs[:2]:  # só os 2 primeiros
                    with pdfplumber.open(p) as pdf:
                        if pdf.pages:
                            t = (pdf.pages[0].extract_text() or "")[:800]
                            preview_pdfs.append({"arquivo": p.name, "trecho": t})
            except Exception as e:
                logger.error("preview falhou: {}", e)
        resultados.append({"tipo": "sulamerica_pme", "n_linhas": len(linhas),
                           "n_beneficiarios": len(grupos),
                           "n_pdfs": len(pdfs),
                           "preview_pdfs": preview_pdfs,
                           "linhas": [l.__dict__ for l in linhas],
                           "grupos": {k: {**v, "cobrancas": [c.__dict__ for c in v["cobrancas"]],
                                          "data_nascimento": v["data_nascimento"].isoformat()}
                                       for k, v in grupos.items()}})
    else:
        for pdf in sorted(Path(pasta_pdfs).glob("*.pdf")):
            registrar_original(pdf, caso_id)
            try:
                boleto, fonte = extrair_hibrido(pdf, usar_vision=usar_vision)
                resultados.append({"pdf": pdf.name, "fonte": fonte,
                                   "boleto": boleto.model_dump(mode="json")})
                logger.info("{} → {}", pdf.name, fonte)
            except Exception as e:
                logger.error("falha em {}: {}", pdf.name, e)
                resultados.append({"pdf": pdf.name, "erro": str(e)})

    out = _caso_dir(caso_id) / "boletos_extraidos.json"
    out.write_text(json.dumps(resultados, ensure_ascii=False, indent=2, default=str),
                   encoding="utf-8")
    registrar_evento(caso_id, "extracao_concluida",
                     {"n_resultados": len(resultados), "arquivo": str(out),
                      "parser": parser_operadora or "hibrido"})
    return resultados


class ExtracaoVaziaError(ValueError):
    """Erro amigável quando o parser não encontra nenhuma cobrança."""
    pass


def _ingerir_via_regex_sap(pdfs, progress_cb=None) -> dict:
    """Roda o regex Sul América PME em todos os PDFs e devolve o MESMO shape
    que `extractors.universal.extrair_pasta` (pra UI/pipeline ficarem agnósticos).
    """
    import pdfplumber
    from .extractors.parsers import sulamerica_pme as sap
    from .extractors.universal import (
        detectar_mes_aniversario, detectar_inicio_vigencia,
    )

    todas: list = []
    apolice_det = None
    estipulante_det = None
    erros: list[str] = []

    for i, pdf in enumerate(pdfs, start=1):
        if progress_cb:
            try:
                progress_cb(i, len(pdfs), pdf.name)
            except Exception:
                pass
        try:
            linhas = sap.extrair_pdf(pdf)
            if not linhas:
                erros.append(f"{pdf.name}: 0 cobranças extraídas")
            todas.extend(linhas)

            # Sniff metadados do cabeçalho do 1º PDF
            if apolice_det is None or estipulante_det is None:
                try:
                    with pdfplumber.open(pdf) as p:
                        txt = (p.pages[0].extract_text() or "") if p.pages else ""
                    import re as _re
                    m = _re.search(r"Ap[óo]lice[:\s]+(\S+)", txt, _re.I)
                    if m and apolice_det is None:
                        apolice_det = m.group(1).strip().rstrip(".:")
                    m = _re.search(r"Raz[ãa]o\s+Social[:\s]+(.+?)(?:\n|$)", txt, _re.I)
                    if m and estipulante_det is None:
                        estipulante_det = m.group(1).strip()
                except Exception:
                    pass
        except Exception as e:
            logger.error("Falha em {}: {}", pdf.name, e)
            erros.append(f"{pdf.name}: {e}")

    todas = sap.reconciliar_cpfs(todas)
    grupos_dc = sap.agrupar_beneficiarios(todas)

    # Serializa LinhaPagamento → dict (formato esperado por construir_caso
    # e pelos detectores de aniversário/vigência)
    grupos: dict[str, dict] = {}
    for k, g in grupos_dc.items():
        grupos[k] = {
            "nome": g["nome"],
            "cpf": g["cpf"],
            "data_nascimento": g["data_nascimento"],
            "data_inicio_vigencia": g.get("data_inicio_vigencia"),
            "parentesco": g["parentesco"],
            "cobrancas": [
                {
                    "competencia": c.competencia,
                    "valor": c.valor,
                    "nome": c.nome,
                    "cpf": c.cpf,
                    "parentesco": c.parentesco,
                    "origem_pdf": c.origem_pdf,
                    "data_nascimento": c.data_nascimento,
                    "data_inicio_vigencia": c.data_inicio_vigencia,
                    "pagina": c.pagina,
                }
                for c in g["cobrancas"]
            ],
        }

    mes_aniv_det, evidencias = detectar_mes_aniversario(grupos)
    inicio_vig_det = detectar_inicio_vigencia(grupos)

    return {
        "tipo": "regex_sulamerica_pme",
        "n_pdfs": len(pdfs),
        "n_linhas": len(todas),
        "n_beneficiarios": len(grupos),
        "operadora_detectada": "Sul América",
        "apolice_detectada": apolice_det,
        "estipulante_detectado": estipulante_det,
        "mes_aniversario_detectado": mes_aniv_det,
        "evidencias_aniversario": evidencias,
        "inicio_vigencia_detectado": inicio_vig_det,
        "erros": erros,
        "grupos": grupos,
    }


def construir_caso(
    caso_id: str,
    pdfs_extracao: dict,
    numero_apolice: str,
    estipulante: str,
    mes_aniversario: int,
    data_vigencia: date,
    operadora: str | None = None,
    tipo_plano: Literal["coletivo_empresarial", "coletivo_adesao", "individual"] = "coletivo_empresarial",
) -> Caso:
    """Construtor GENÉRICO de Caso, funciona com qualquer operadora.

    Aceita o dict produzido por `extractors.universal.extrair_pasta` (Vision)
    OU `extractors.parsers.sulamerica_pme.agrupar_beneficiarios` (regex).
    Estrutura esperada: `pdfs_extracao["grupos"]` = {chave: {nome, cpf, ...}}.
    """
    grupos = pdfs_extracao.get("grupos") or {}
    operadora = operadora or pdfs_extracao.get("operadora_detectada") or "Desconhecida"

    benefs: list[Beneficiario] = []
    cobrancas: list[ParcelaCobrada] = []

    for k, g in grupos.items():
        ls = sorted(g["cobrancas"], key=lambda x: x["competencia"])
        if not ls:
            continue
        cpf = g.get("cpf") or "00000000191"
        try:
            nasc = g.get("data_nascimento") or date(1970, 1, 1)
            if isinstance(nasc, str):
                nasc = date.fromisoformat(nasc)
            b = Beneficiario(
                nome=g["nome"], cpf=cpf, data_nascimento=nasc,
                mensalidade_base=Decimal(str(ls[0]["valor"])),
                titular=(g.get("parentesco") == "TITULAR"),
            )
        except Exception as e:
            logger.warning("SKIP {}: {}", g.get("nome", "?"), e)
            continue
        benefs.append(b)
        for l in ls:
            cobrancas.append(ParcelaCobrada(
                competencia=l["competencia"],
                valor_cobrado=Decimal(str(l["valor"])),
                beneficiario_cpf=b.cpf,
                operadora=operadora,
                origem="pdf",
            ))

    if not benefs:
        n_pdfs = pdfs_extracao.get("n_pdfs", 0)
        raise ExtracaoVaziaError(
            f"Não consegui identificar beneficiários em nenhum dos {n_pdfs} PDFs. "
            f"Verifique se os arquivos são demonstrativos de pagamento "
            f"(não comprovantes bancários simples) e tente novamente. "
            f"Se o problema persistir, envie 1 PDF de exemplo para "
            f"laurazandavalle.adv@gmail.com."
        )

    dt_ass = data_vigencia.replace(month=mes_aniversario, day=1)
    contrato = Contrato(
        numero=numero_apolice,
        operadora=operadora,
        data_assinatura=dt_ass,
        mensalidade_inicial=min(b.mensalidade_base for b in benefs),
        tipo=tipo_plano,
        n_vidas=len(benefs),
        estipulante=estipulante,
    )
    return Caso(caso_id=caso_id, contrato=contrato, beneficiarios=benefs, cobrancas=cobrancas)


def construir_caso_sulamerica_pme(
    caso_id: str,
    pdfs_extracao: dict,
    numero_apolice: str,
    estipulante: str,
    mes_aniversario: int,
    data_vigencia: date,
    tipo_plano: Literal["coletivo_empresarial", "coletivo_adesao", "individual"] = "coletivo_empresarial",
) -> Caso:
    """Monta objeto Caso a partir da extração SulAmérica PME."""
    grupos = pdfs_extracao.get("grupos") or {}
    benefs: list[Beneficiario] = []
    cobrancas: list[ParcelaCobrada] = []

    for k, g in grupos.items():
        ls = sorted(g["cobrancas"], key=lambda x: x["competencia"])
        if not ls:
            continue
        cpf = g["cpf"] or "00000000191"
        try:
            nasc = g["data_nascimento"]
            if isinstance(nasc, str):
                nasc = date.fromisoformat(nasc)
            b = Beneficiario(
                nome=g["nome"], cpf=cpf, data_nascimento=nasc,
                mensalidade_base=Decimal(str(ls[0]["valor"])),
                titular=(g["parentesco"] == "TITULAR"),
            )
        except Exception as e:
            logger.warning("SKIP {}: {}", g["nome"], e)
            continue
        benefs.append(b)
        for l in ls:
            cobrancas.append(ParcelaCobrada(
                competencia=l["competencia"], valor_cobrado=Decimal(str(l["valor"])),
                beneficiario_cpf=b.cpf, operadora="SulAmerica", origem="pdf",
            ))

    if not benefs:
        n_pdfs = pdfs_extracao.get("n_pdfs", 0)
        raise ExtracaoVaziaError(
            f"Não consegui identificar beneficiários nos PDFs enviados "
            f"({n_pdfs} arquivos processados). "
            f"O parser atual reconhece o formato 'Sul América Saúde OnLine / "
            f"Relatório de Faturamento PME'. Se o seu caso é de outra operadora "
            f"(Bradesco, Amil, Unimed, Hapvida, Notre Dame, individual etc.), "
            f"envie um PDF de exemplo para laurazandavalle.adv@gmail.com — "
            f"adicionamos suporte rapidamente."
        )

    # Mês aniversário derivado da data_vigencia, mas sobrescrito por parâmetro
    dt_ass = data_vigencia.replace(month=mes_aniversario, day=1)
    contrato = Contrato(
        numero=numero_apolice, operadora="SulAmerica",
        data_assinatura=dt_ass,
        mensalidade_inicial=min(b.mensalidade_base for b in benefs),
        tipo=tipo_plano, n_vidas=len(benefs),
        estipulante=estipulante,
    )
    return Caso(caso_id=caso_id, contrato=contrato, beneficiarios=benefs, cobrancas=cobrancas)


def salvar_caso(caso: Caso) -> Path:
    out = _caso_dir(caso.caso_id) / "caso.json"
    out.write_text(caso.model_dump_json(indent=2), encoding="utf-8")
    registrar_evento(caso.caso_id, "caso_salvo", {"arquivo": str(out)})
    return out


def carregar_caso(caso_id: str) -> Caso:
    p = _caso_dir(caso_id) / "caso.json"
    return Caso.model_validate_json(p.read_text(encoding="utf-8"))


# ───────────────────────── CÁLCULO ─────────────────────────

def calcular(
    caso: Caso,
    data_distribuicao_acao: date | None = None,
    meses_restituicao: int = 36,
    projetar_ate_distribuicao: bool = True,
    excluir_saidos_antes: date | None = None,
) -> dict[Beneficiario, list[LinhaSubstituicao]]:
    """Executa motor de SUBSTITUIÇÃO por beneficiário.

    Parâmetros:
    - `projetar_ate_distribuicao`: se True, preenche meses entre última cobrança
      e data de distribuição com o último valor pago (padrão pericial).
    - `excluir_saidos_antes`: se definido, exclui beneficiários cuja ÚLTIMA
      cobrança seja anterior a essa data (ex.: excluir vidas que saíram em 2015
      do pedido por prescrição já consumada).
    """
    hoje = data_distribuicao_acao or date.today()
    proj = hoje if projetar_ate_distribuicao else None
    resultados: dict[Beneficiario, list[LinhaSubstituicao]] = {}

    cobrancas_por_cpf: dict[str, list[ParcelaCobrada]] = {}
    cobrancas_sem_cpf: list[ParcelaCobrada] = []
    for c in caso.cobrancas:
        if c.beneficiario_cpf:
            cobrancas_por_cpf.setdefault(c.beneficiario_cpf, []).append(c)
        else:
            cobrancas_sem_cpf.append(c)

    for b in caso.beneficiarios:
        cobr = cobrancas_por_cpf.get(b.cpf, []) + cobrancas_sem_cpf
        if not cobr:
            continue
        if excluir_saidos_antes:
            # Usa última cobrança REAL (em `caso.cobrancas`), não projeção
            ultima = max(c.competencia for c in cobr)
            y, m = (int(x) for x in ultima.split("-"))
            if date(y, m, 1) < excluir_saidos_antes:
                logger.info("Excluindo {} (última cobrança real {} < corte)", b.nome, ultima)
                continue
        resultados[b] = motor_substituicao(
            b, caso.contrato, cobr, hoje=hoje,
            meses_restituicao=meses_restituicao, projetar_ate=proj,
        )
    totais = {
        b.nome: {k: str(v) for k, v in totalizar_substituicao(ls).items()}
        for b, ls in resultados.items()
    }
    registrar_evento(caso.caso_id, "calculo_substituicao", {"totais": totais})
    return resultados


def correcao_monetaria_agregada(
    resultados: dict[Beneficiario, list[LinhaSubstituicao]],
    data_alvo: date,
    indices: list[TipoIndice] | None = None,
) -> dict:
    """Agrega diferenças mensais e aplica todos os índices."""
    indices = indices or ["INPC", "IPCA", "IPCA-E", "IGP-M", "SELIC", "TR", "POUPANCA", "TJSP"]
    diff_por_mes: dict[str, Decimal] = {}
    for _, ls in resultados.items():
        for l in ls:
            if l.diferenca > 0:
                diff_por_mes[l.competencia] = diff_por_mes.get(l.competencia, Decimal("0")) + l.diferenca

    totais: dict[str, Decimal] = {k: Decimal("0") for k in indices}
    erros: dict[str, int] = {k: 0 for k in indices}
    juros = Decimal("0")
    for comp, diff in sorted(diff_por_mes.items()):
        y, m = (int(x) for x in comp.split("-"))
        dt = date(y, m, 1)
        juros += juros_1pct_am(diff, dt, data_alvo)
        for ind in indices:
            try:
                totais[ind] += atualizar(diff, dt, data_alvo, ind)
            except Exception:
                erros[ind] += 1
                totais[ind] += diff

    return {
        "data_alvo": data_alvo.isoformat(),
        "diferenca_historica": sum(diff_por_mes.values(), Decimal("0")),
        "diferenca_por_mes": diff_por_mes,
        "totais_por_indice": totais,
        "erros_por_indice": erros,
        "juros_1pct_am": juros,
        "combinacoes": {
            "TJSP_mais_juros_1pct": totais.get("TJSP", Decimal("0")) + juros,
            "INPC_mais_juros_1pct": totais.get("INPC", Decimal("0")) + juros,
        },
    }


# ───────────────────────── OUTPUTS ─────────────────────────

def gerar_planilha_caso(
    caso: Caso,
    resultados: dict[Beneficiario, list[LinhaSubstituicao]],
    correcao: dict | None = None,
    indice_correcao_principal: TipoIndice = "TJSP",
    data_distribuicao: date | None = None,
) -> Path:
    """Gera planilha XLSX usando o template oficial do escritório.

    O template é parte obrigatória da entrega aos mentorandos. Se ele falhar,
    a exceção deve aparecer na UI em vez de gerar silenciosamente uma planilha
    fora do modelo.
    """
    saida = _caso_dir(caso.caso_id) / f"calculo_{caso.caso_id}.xlsx"
    from .writers.xlsx_template import TEMPLATE_PATH
    logger.info("Template oficial path: {} (existe={})", TEMPLATE_PATH, TEMPLATE_PATH.exists())
    gerar_planilha_template(
        saida=saida, caso=caso, resultados=resultados,
        correcao=correcao,
    )
    logger.info("Planilha gerada via TEMPLATE OFICIAL (13 abas): {}", saida)
    registrar_evento(caso.caso_id, "planilha_gerada",
                     {"arquivo": str(saida), "writer": "template_oficial"})
    return saida


def gerar_peticao_caso(
    caso: Caso,
    resultados: dict[Beneficiario, list[LinhaSubstituicao]],
    correcao: dict | None = None,
    contexto_extra: dict | None = None,
) -> Path:
    from .writers.peticao import gerar_peticao as _gerar

    tot_hist = sum(
        totalizar_substituicao(ls)["diferenca"] for _, ls in resultados.items()
    )
    tot_corr = correcao["combinacoes"]["TJSP_mais_juros_1pct"] if correcao else tot_hist
    ctx = {
        "autor": ", ".join(b.nome for b in caso.beneficiarios),
        "re": caso.contrato.operadora,
        "fatos": (
            f"Contrato coletivo empresarial (apólice {caso.contrato.numero}) "
            f"firmado em {caso.contrato.data_assinatura.strftime('%d/%m/%Y')} "
            f"com {caso.contrato.n_vidas} vidas — FALSO COLETIVO (REsp 1.553.013/RJ)."
        ),
        "total_devido": f"R$ {tot_corr:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
        "valor_causa": "a ser liquidado",
        "cidade": "Porto Alegre",
        "data": date.today().strftime("%d de %B de %Y"),
        "advogado": "Laura Zandavalle", "oab": "RS/—",
    }
    if contexto_extra:
        ctx.update(contexto_extra)
    saida = _caso_dir(caso.caso_id) / f"peticao_{caso.caso_id}.docx"
    _gerar(saida, ctx, template=settings.templates_dir / "peticao_inicial.docx")
    registrar_evento(caso.caso_id, "peticao_gerada", {"arquivo": str(saida)})
    return saida


def executar_caso_completo(
    caso: Caso,
    data_distribuicao_acao: date,
    indice_principal: TipoIndice = "TJSP",
    excluir_saidos_antes: date | None = None,
) -> dict:
    """Orquestra: calcular + correção + planilha + petição. Retorna dict com paths."""
    t_total = time.perf_counter()
    t = time.perf_counter()
    resultados = calcular(
        caso, data_distribuicao_acao=data_distribuicao_acao,
        excluir_saidos_antes=excluir_saidos_antes,
    )
    logger.info("tempo_calculo_substituicao={:.2f}s caso={}", time.perf_counter() - t, caso.caso_id)
    t = time.perf_counter()
    correcao = correcao_monetaria_agregada(resultados, data_distribuicao_acao)
    logger.info("tempo_correcao_monetaria={:.2f}s caso={}", time.perf_counter() - t, caso.caso_id)
    t = time.perf_counter()
    xlsx = gerar_planilha_caso(caso, resultados, correcao, indice_principal, data_distribuicao_acao)
    logger.info("tempo_planilha_xlsx={:.2f}s caso={}", time.perf_counter() - t, caso.caso_id)
    t = time.perf_counter()
    try:
        docx = gerar_peticao_caso(caso, resultados, correcao)
    except Exception as e:
        logger.warning("petição falhou: {}", e)
        docx = None
    logger.info("tempo_peticao_docx={:.2f}s caso={}", time.perf_counter() - t, caso.caso_id)

    relat = _caso_dir(caso.caso_id) / "relatorio_correcoes.json"
    relat.write_text(json.dumps({
        "caso_id": caso.caso_id,
        "data_distribuicao": data_distribuicao_acao.isoformat(),
        "indice_principal": indice_principal,
        **{k: str(v) if isinstance(v, Decimal) else v for k, v in correcao.items()
           if k != "diferenca_por_mes"},
        "totais_por_indice": {k: str(v) for k, v in correcao["totais_por_indice"].items()},
    }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    logger.info("tempo_total_pipeline={:.2f}s caso={}", time.perf_counter() - t_total, caso.caso_id)

    return {
        "resultados": resultados,
        "correcao": correcao,
        "xlsx": xlsx,
        "docx": docx,
        "relatorio": relat,
    }
