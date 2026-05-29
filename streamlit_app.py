"""Calculadora de Reajuste Abusivo — versão web (Streamlit Cloud).

Entry point do deploy. Adiciona `src/` ao sys.path antes de importar o pacote
`advreajuste` — assim o Streamlit Cloud roda sem precisar instalar o projeto
como pacote.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import hmac
import os
import re
import shutil
from datetime import date, datetime

import streamlit as st

from advreajuste import pipeline
from advreajuste.calculators.substituicao import totalizar_substituicao
from advreajuste.config import settings


# ─────────────────────── Config página ───────────────────────
st.set_page_config(
    page_title="Calculadora de Reajuste | Laura Zopelaro",
    page_icon="⚖️",
    layout="centered",
    menu_items={
        "About": "Calculadora de reajuste abusivo de planos de saúde — "
                 "Laura Zopelaro Advocacia. Conforme STJ Tema 1016.",
        "Get help": None,
        "Report a bug": None,
    },
)

# CSS "cara de software"
st.markdown("""
<style>
    /* Layout */
    .main .block-container { max-width: 780px; padding-top: 3rem; padding-bottom: 5rem; }

    /* Hero */
    .hero {
        background: linear-gradient(135deg, #1e3a8a 0%, #2563eb 100%);
        color: white; padding: 2rem 2rem 1.5rem 2rem;
        border-radius: 14px; margin-bottom: 2rem;
        box-shadow: 0 10px 30px rgba(37, 99, 235, 0.2);
    }
    .hero h1 { color: white !important; margin: 0 0 .3rem 0; font-size: 1.8rem; }
    .hero p { color: #dbeafe; margin: 0; font-size: .95rem; }

    /* Steps */
    .step-num {
        display: inline-flex; align-items: center; justify-content: center;
        width: 28px; height: 28px; border-radius: 50%;
        background: #2563eb; color: white; font-weight: 700; font-size: .85rem;
        margin-right: .6rem;
    }
    .step-title { display: flex; align-items: center; margin: 1.5rem 0 .8rem 0;
                  font-size: 1.1rem; font-weight: 600; color: #1e293b; }

    /* Buttons */
    .stButton>button { width: 100%; padding: 0.85rem; font-size: 1.05rem;
                        font-weight: 600; border-radius: 8px; }
    .stButton>button[kind="primary"] {
        background: linear-gradient(135deg, #2563eb 0%, #1e3a8a 100%);
        border: none;
    }

    /* File uploader */
    div[data-testid="stFileUploader"] { border: 2px dashed #cbd5e1;
                                         border-radius: 10px; padding: 1.2rem; }

    /* Result cards */
    .big-num { font-size: 2.2rem; font-weight: 700; color: #1e3a8a;
               margin: .3rem 0; letter-spacing: -0.02em; }
    .result-card {
        background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 12px;
        padding: 1.5rem; text-align: center;
    }
    .result-card .label { color: #64748b; font-size: .85rem;
                           text-transform: uppercase; letter-spacing: 0.05em; }

    /* Loading pulse */
    @keyframes pulse {
        0%, 100% { opacity: 1; }
        50% { opacity: 0.6; }
    }
    .loading { animation: pulse 1.5s ease-in-out infinite; }

    /* Hide Streamlit branding */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
</style>
""", unsafe_allow_html=True)


# ─────────────────────── Autenticação ───────────────────────
def check_password() -> bool:
    """Senha via variável de ambiente ou st.secrets. Em dev local sem senha, pula."""
    senha_correta = os.environ.get("APP_PASSWORD")

    try:
        senha_correta = senha_correta or st.secrets.get("app_password")
    except Exception:
        pass

    if not senha_correta:
        return True  # dev local sem secrets.toml

    if st.session_state.get("auth_ok"):
        return True

    st.markdown("""
    <div class="hero">
        <h1>⚖️ Calculadora de Reajuste</h1>
        <p>Acesso restrito — informe a senha enviada pela Laura.</p>
    </div>
    """, unsafe_allow_html=True)

    senha = st.text_input("Senha", type="password", label_visibility="collapsed",
                           placeholder="Senha de acesso")
    if st.button("Entrar", type="primary"):
        if hmac.compare_digest(senha or "", str(senha_correta)):
            st.session_state.auth_ok = True
            st.rerun()
        else:
            st.error("Senha incorreta.")
    st.caption("Se não tiver a senha, entre em contato: laurazandavalle.adv@gmail.com")
    return False


if not check_password():
    st.stop()


# ─────────────────────── Estado ───────────────────────
if "saida" not in st.session_state:
    st.session_state.saida = None
if "erro" not in st.session_state:
    st.session_state.erro = None
if "upload_key" not in st.session_state:
    st.session_state.upload_key = 0
if "processar_pendente" not in st.session_state:
    st.session_state.processar_pendente = None


def slug(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]", "_", s.strip().lower())
    return re.sub(r"_+", "_", s).strip("_") or "caso"


def _nome_pdf_seguro(nome: str, usados: set[str]) -> str:
    """Normaliza nomes de upload e evita sobrescrita quando há nomes repetidos."""
    base = Path(nome).name
    stem = slug(Path(base).stem) or "documento"
    candidato = f"{stem}.pdf"
    n = 2
    while candidato.lower() in usados:
        candidato = f"{stem}_{n}.pdf"
        n += 1
    usados.add(candidato.lower())
    return candidato


def _salvar_uploads_em_disco(uploads, pasta: Path) -> list[Path]:
    """Salva uploads em streaming, sem duplicar cada PDF inteiro em memória."""
    if pasta.exists():
        shutil.rmtree(pasta)
    pasta.mkdir(parents=True, exist_ok=True)

    salvos: list[Path] = []
    usados: set[str] = set()
    for up in uploads:
        destino = pasta / _nome_pdf_seguro(up.name, usados)
        try:
            up.seek(0)
        except Exception:
            pass
        with destino.open("wb") as f:
            shutil.copyfileobj(up, f, length=1024 * 1024)
        salvos.append(destino)
    return salvos


# ─────────────────────── Hero ───────────────────────
st.markdown("""
<div class="hero">
    <h1>⚖️ Calculadora de Reajuste</h1>
    <p>Cálculo pericial automatizado de planos de saúde — STJ Tema 1016.</p>
</div>
""", unsafe_allow_html=True)


MESES_PT = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
            "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]


# ─────────────────────── Único passo: upload + 1 botão ───────────────────────
st.markdown('<div class="step-title"><span class="step-num">1</span>Envie os demonstrativos de pagamento</div>',
            unsafe_allow_html=True)
st.caption(
    "📌 **Apenas os demonstrativos** (faturas / boletos / relatórios de cobrança "
    "com nome dos beneficiários e valor). **Não precisa enviar contrato.** "
    "Funciona em qualquer operadora — Sul América, Bradesco, Amil, Unimed, "
    "Hapvida, Notre Dame, Care Plus etc. Apólice, estipulante, vigência, "
    "beneficiários e mês de aniversário são detectados automaticamente."
)

ups = st.file_uploader(
    " ", type=["pdf"], accept_multiple_files=True,
    label_visibility="collapsed",
    key=f"pdf_upload_{st.session_state.upload_key}",
)
if ups:
    total_mb = sum(getattr(up, "size", 0) or 0 for up in ups) / (1024 * 1024)
    st.success(f"✅ {len(ups)} PDF(s) pronto(s)")
    if len(ups) >= 80 or total_mb >= 120:
        st.info(
            f"📦 Lote grande: {len(ups)} PDFs / {total_mb:.1f} MB. "
            "Vou salvar os arquivos primeiro e processar em uma segunda etapa "
            "para evitar estouro de memória no Streamlit."
        )
    else:
        st.caption(f"⏱️ Tempo estimado: alguns segundos ({len(ups)} PDFs).")


# ─── Configurações avançadas (escondidas) ───
with st.expander("⚙️ Configurações avançadas (opcional — só altere se precisar)"):
    nome_caso_input = st.text_input(
        "Nome do caso (opcional)",
        placeholder="Sugerido a partir do estipulante detectado",
        help="Identificador curto. Se vazio, gerado automaticamente do nome "
             "da empresa estipulante.",
    )
    data_acao = st.date_input(
        "Data da ação", value=date.today(), format="DD/MM/YYYY",
        help="Quando a ação será protocolada. Usado para calcular prescrição "
             "trienal e correção monetária.",
    )
    forcar_mes_aniv = st.selectbox(
        "Forçar mês de aniversário (se a auto-detecção falhar)",
        ["(detectar automaticamente)"] + MESES_PT,
        help="Por padrão a IA detecta olhando saltos de valor entre meses "
             "consecutivos. Se você souber e quiser garantir, escolha aqui.",
    )

def _processar_pasta_salva(job: dict):
    caso_id_provisorio = job["caso_id_provisorio"]
    pasta = Path(job["pasta"])
    nome_caso_job = job.get("nome_caso_input") or ""
    data_acao_job = date.fromisoformat(job["data_acao"])
    forcar_mes_job = job["forcar_mes_aniv"]
    n_pdfs_job = job.get("n_pdfs", len(list(pasta.glob("*.pdf"))))

    with st.status("🔄 Processando...", expanded=True) as status:
        st.write(f"📄 Lendo {n_pdfs_job} PDFs (extração local, sem API)...")
        prog = st.progress(0.0, text="Iniciando...")

        def _on_progress(i, total, nome):
            prog.progress(i / total, text=f"PDF {i}/{total} — {nome[:50]}")

        extr = pipeline.ingerir_pdfs(
            caso_id_provisorio, pasta, parser_operadora="universal",
            progress_cb=_on_progress,
        )
        prog.empty()
        r = extr[0] if extr else {}

        op_det = r.get("operadora_detectada") or "(não identificada)"
        apolice_det = r.get("apolice_detectada") or "—"
        estipulante_det = r.get("estipulante_detectado") or "—"
        mes_aniv_det = r.get("mes_aniversario_detectado")
        inicio_vig_det = r.get("inicio_vigencia_detectado")

        if not nome_caso_job and estipulante_det != "—":
            caso_id = slug(f"{estipulante_det}_{date.today().year}")
        else:
            caso_id = caso_id_provisorio

        if forcar_mes_job != "(detectar automaticamente)":
            mes_aniv_final = MESES_PT.index(forcar_mes_job) + 1
            origem_mes = "selecionado por você"
        elif mes_aniv_det:
            mes_aniv_final = mes_aniv_det
            origem_mes = "detectado pela IA"
        else:
            mes_aniv_final = 1
            origem_mes = "fallback (NÃO detectado — resultado pode estar errado)"

        if inicio_vig_det:
            if isinstance(inicio_vig_det, str):
                try:
                    inicio_vig_det = date.fromisoformat(inicio_vig_det)
                except Exception:
                    inicio_vig_det = None
        vigencia_final = inicio_vig_det or date(2010, 1, 1)

        st.write(f"   ✓ Operadora: **{op_det}**")
        st.write(f"   ✓ Apólice: **{apolice_det}** · Estipulante: **{estipulante_det}**")
        st.write(f"   ✓ Vigência: **{vigencia_final.strftime('%d/%m/%Y')}**"
                 + ("" if inicio_vig_det else " (fallback)"))
        st.write(f"   ✓ Mês de aniversário: **{MESES_PT[mes_aniv_final-1]}** ({origem_mes})")
        st.write(f"   ✓ {r.get('n_linhas', 0)} cobranças de "
                 f"{r.get('n_beneficiarios', 0)} beneficiários.")
        if r.get("erros"):
            erros_filtrados = [e for e in r["erros"] if "0 cobranças" not in e]
            if erros_filtrados:
                st.warning(f"   ⚠️ {len(erros_filtrados)} PDF(s) falharam: " +
                           ", ".join(erros_filtrados[:3]) +
                           ("..." if len(erros_filtrados) > 3 else ""))

        st.write("📋 Construindo caso...")
        tipo_plano_det = (
            r.get("tipo_plano_detectado")
            or ("coletivo_empresarial" if estipulante_det != "—" else "individual")
        )
        caso = pipeline.construir_caso(
            caso_id=caso_id, pdfs_extracao=r,
            numero_apolice=apolice_det, estipulante=estipulante_det,
            mes_aniversario=mes_aniv_final,
            data_vigencia=vigencia_final,
            operadora=op_det,
            tipo_plano=tipo_plano_det,
        )
        pipeline.salvar_caso(caso)
        st.write(f"   ✓ {len(caso.beneficiarios)} beneficiários, falso coletivo: "
                 f"{caso.contrato.falso_coletivo}")

        st.write("🧮 Calculando reajustes e aplicando correção monetária...")
        corte = date(data_acao_job.year - 3, data_acao_job.month, 1)
        saida = pipeline.executar_caso_completo(
            caso, data_distribuicao_acao=data_acao_job,
            indice_principal="TJSP", excluir_saidos_antes=corte,
        )
        st.session_state.saida = saida
        st.session_state.caso = caso
        st.session_state.evidencias_aniversario = r.get("evidencias_aniversario", [])
        st.session_state.mes_aniv_origem = origem_mes
        st.session_state.processar_pendente = None
        status.update(label="✅ Cálculo concluído!", state="complete")


if st.session_state.processar_pendente and not st.session_state.saida and not st.session_state.erro:
    try:
        _processar_pasta_salva(st.session_state.processar_pendente)
        st.rerun()
    except pipeline.ExtracaoVaziaError as e:
        st.session_state.processar_pendente = None
        st.session_state.erro = ("zero_extracao", str(e))
    except Exception as e:
        import traceback
        st.session_state.processar_pendente = None
        st.session_state.erro = ("erro_geral", str(e))
        st.session_state.traceback = traceback.format_exc()


pronto = bool(ups)

if not pronto:
    st.caption("💡 Envie ao menos 1 PDF para liberar o botão.")

if st.button("🚀 Analisar PDFs e gerar planilha", type="primary", disabled=not pronto,
              use_container_width=True):
    st.session_state.erro = None
    st.session_state.saida = None
    # Caso_id provisório — único por execução e refinado após detectar estipulante.
    base_caso = slug(nome_caso_input or f"caso_{date.today().isoformat()}")
    caso_id_provisorio = f"{base_caso}_{datetime.now().strftime('%H%M%S')}"
    try:
        pasta = settings.casos_dir / caso_id_provisorio / "pdfs"
        pdfs_salvos = _salvar_uploads_em_disco(ups, pasta)

        st.session_state.processar_pendente = {
            "caso_id_provisorio": caso_id_provisorio,
            "pasta": str(pasta),
            "nome_caso_input": nome_caso_input,
            "data_acao": data_acao.isoformat(),
            "forcar_mes_aniv": forcar_mes_aniv,
            "n_pdfs": len(pdfs_salvos),
        }
        st.session_state.upload_key += 1
        st.rerun()
    except pipeline.ExtracaoVaziaError as e:
        st.session_state.erro = ("zero_extracao", str(e))
    except Exception as e:
        import traceback
        st.session_state.erro = ("erro_geral", str(e))
        st.session_state.traceback = traceback.format_exc()


# ─────────────────────── Erro ───────────────────────
if st.session_state.erro:
    tipo, msg = (st.session_state.erro
                  if isinstance(st.session_state.erro, tuple)
                  else ("erro_geral", st.session_state.erro))

    if tipo == "zero_extracao":
        st.warning("⚠️ **Não consegui identificar beneficiários nos PDFs**")
        st.markdown(msg)
        st.info(
            "💡 **Causas comuns:**\n\n"
            "1. **PDFs escaneados de baixa qualidade** (texto não selecionável). "
            "Tente exportar de novo do portal da operadora.\n"
            "2. **Comprovantes bancários simples** (DDA, recibo) sem detalhamento "
            "por beneficiário — só com o valor total. Esses não servem.\n"
            "3. **Formato ainda não suportado.** Encaminhe 1 PDF de exemplo para "
            "`laurazandavalle.adv@gmail.com` que adicionamos suporte rapidamente."
        )
    else:
        st.error(f"❌ {msg}")
        with st.expander("Detalhes técnicos"):
            st.code(st.session_state.get("traceback", ""))


# ─────────────────────── Resultado ───────────────────────
if st.session_state.saida:
    saida = st.session_state.saida
    caso = st.session_state.caso
    cor = saida["correcao"]

    st.markdown("---")
    st.markdown("### 📊 Resultado")

    total_rest = sum(
        totalizar_substituicao(ls)["restituivel_simples"]
        for _, ls in saida["resultados"].items()
    )
    total_corrigido = cor["combinacoes"]["TJSP_mais_juros_1pct"]

    c1, c2 = st.columns(2)
    with c1:
        st.markdown(f"""
        <div class="result-card">
            <div class="label">Restituição trienal</div>
            <div class="big-num">R$ {float(total_rest):,.2f}</div>
            <div style="color:#64748b; font-size:.85rem;">Valor histórico (sem correção)</div>
        </div>
        """, unsafe_allow_html=True)
    with c2:
        st.markdown(f"""
        <div class="result-card" style="background:#eff6ff; border-color:#2563eb;">
            <div class="label">Com TJSP + juros 1% a.m.</div>
            <div class="big-num">R$ {float(total_corrigido):,.2f}</div>
            <div style="color:#64748b; font-size:.85rem;">Valor corrigido até a data da ação</div>
        </div>
        """, unsafe_allow_html=True)

    # Resumo simples por vida
    import pandas as pd
    linhas = []
    for b, ls in saida["resultados"].items():
        t = totalizar_substituicao(ls)
        linhas.append({
            "Beneficiário": b.nome,
            "Meses": t["n_meses"],
            "Reaj. abusivos": t["n_aniversarios_abusivos"],
            "Restituição": float(t["restituivel_simples"]),
        })
    if linhas:
        df = pd.DataFrame(linhas)
        st.markdown("#### Por beneficiário")
        st.dataframe(
            df, hide_index=True, use_container_width=True,
            column_config={
                "Restituição": st.column_config.NumberColumn(
                    format="R$ %.2f",
                ),
            },
        )

    # Evidências da auto-detecção do mês de aniversário (transparência)
    evid = st.session_state.get("evidencias_aniversario") or []
    if evid:
        with st.expander(f"🔍 Como detectei o mês de aniversário ({len(evid)} saltos analisados)"):
            st.caption(
                "A IA olha cada beneficiário e identifica em que mês a "
                "mensalidade pulou entre 3% e 40% (faixa típica de reajuste "
                "anual). O mês com maioria de saltos vira o mês de aniversário."
            )
            for e in evid[:30]:
                st.text(e)
            if len(evid) > 30:
                st.caption(f"... +{len(evid)-30} saltos não exibidos")

    with st.expander("📈 Ver todos os índices de correção"):
        cor_linhas = []
        for k, v in cor["totais_por_indice"].items():
            cor_linhas.append({"Índice": k, "Valor corrigido": float(v)})
        for k, v in cor["combinacoes"].items():
            cor_linhas.append({"Índice": k, "Valor corrigido": float(v)})
        st.dataframe(
            pd.DataFrame(cor_linhas), hide_index=True, use_container_width=True,
            column_config={"Valor corrigido": st.column_config.NumberColumn(format="R$ %.2f")},
        )

    st.markdown("### ⬇️ Baixar arquivos")
    d1, d2 = st.columns(2)
    with d1:
        if saida.get("xlsx") and saida["xlsx"].exists():
            st.download_button(
                "📊 Planilha de cálculo (XLSX)",
                data=saida["xlsx"].read_bytes(),
                file_name=saida["xlsx"].name,
                use_container_width=True, type="primary",
            )
    with d2:
        if saida.get("docx") and saida["docx"].exists():
            st.download_button(
                "📄 Minuta da petição (DOCX)",
                data=saida["docx"].read_bytes(),
                file_name=saida["docx"].name,
                use_container_width=True,
            )

    st.caption("⚠️ A minuta é um rascunho auxiliar — revise integralmente antes de protocolar "
               "(Rec. OAB 001/2024).")

    if st.button("🔄 Calcular outro caso"):
        st.session_state.saida = None
        st.session_state.erro = None
        st.rerun()


# ─────────────────────── Footer ───────────────────────
st.markdown("""
<div style="text-align:center; color:#94a3b8; font-size:.8rem; margin-top:3rem;">
Laura Zopelaro Advocacia • Uso restrito aos mentorandos<br>
Supervisão humana obrigatória (Rec. OAB/CFOAB 001/2024)
</div>
""", unsafe_allow_html=True)
