"""Calculadora de Reajuste Abusivo — versão web (Streamlit Cloud)."""
from __future__ import annotations

import hmac
import re
from datetime import date
from pathlib import Path

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
    """Senha via st.secrets. Em dev local, se não houver secret, pula."""
    try:
        senha_correta = st.secrets.get("app_password")
    except Exception:
        senha_correta = None

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


def slug(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]", "_", s.strip().lower())
    return re.sub(r"_+", "_", s).strip("_") or "caso"


# ─────────────────────── Hero ───────────────────────
st.markdown("""
<div class="hero">
    <h1>⚖️ Calculadora de Reajuste</h1>
    <p>Cálculo pericial automatizado de planos de saúde — STJ Tema 1016.</p>
</div>
""", unsafe_allow_html=True)


# ─────────────────────── 1. Upload ───────────────────────
st.markdown('<div class="step-title"><span class="step-num">1</span>Envie os PDFs</div>',
            unsafe_allow_html=True)
st.caption("Demonstrativos de pagamento da operadora. Pode enviar vários PDFs juntos.")

ups = st.file_uploader(
    " ", type=["pdf"], accept_multiple_files=True,
    label_visibility="collapsed",
)
if ups:
    st.success(f"✅ {len(ups)} PDF(s) pronto(s): " +
               ", ".join(u.name[:25] + ("..." if len(u.name) > 25 else "") for u in ups[:5]) +
               (f" +{len(ups) - 5}" if len(ups) > 5 else ""))


# ─────────────────────── 2. Dados do contrato ───────────────────────
st.markdown('<div class="step-title"><span class="step-num">2</span>Dados do contrato</div>',
            unsafe_allow_html=True)

col1, col2 = st.columns(2)
with col1:
    nome_caso = st.text_input("Nome do caso", placeholder="Ex.: Silva 2025",
                               help="Identificador curto — usado só pra organizar.")
    apolice = st.text_input("Nº da apólice / contrato", placeholder="Ex.: 610047973")
with col2:
    estipulante = st.text_input("Nome da empresa estipulante",
                                 placeholder="Ex.: Silva & Silva Ltda")
    vigencia = st.date_input(
        "Início da vigência", value=date(2010, 1, 1),
        min_value=date(1990, 1, 1), max_value=date.today(),
        format="DD/MM/YYYY",
    )

col3, col4 = st.columns(2)
with col3:
    mes_aniv = st.selectbox(
        "Mês do reajuste anual",
        list(range(1, 13)),
        format_func=lambda m: [
            "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
            "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
        ][m - 1],
        index=6,
        help="Mês em que a mensalidade reajusta todo ano. Descubra comparando "
             "duas mensalidades seguidas e achando o salto.",
    )
with col4:
    data_acao = st.date_input(
        "Data da ação", value=date.today(), format="DD/MM/YYYY",
        help="Quando a ação será protocolada. Usado para calcular a "
             "prescrição trienal (últimos 3 anos).",
    )


# ─────────────────────── 3. Calcular ───────────────────────
st.markdown('<div class="step-title"><span class="step-num">3</span>Gerar planilha</div>',
            unsafe_allow_html=True)

pronto = bool(ups and nome_caso and apolice and estipulante)

if not pronto:
    st.caption("💡 Preencha os campos acima para liberar o botão.")

if st.button("🚀 Calcular e gerar planilha", type="primary", disabled=not pronto,
              use_container_width=True):
    caso_id = slug(nome_caso)
    st.session_state.erro = None
    st.session_state.saida = None
    try:
        pasta = settings.casos_dir / caso_id / "pdfs"
        pasta.mkdir(parents=True, exist_ok=True)
        for up in ups:
            (pasta / up.name).write_bytes(up.getvalue())

        with st.status("🔄 Processando...", expanded=True) as status:
            st.write("📄 Lendo PDFs...")
            extr = pipeline.ingerir_pdfs(caso_id, pasta, parser_operadora="sulamerica_pme")
            r = extr[0] if extr else {}
            st.write(f"   ✓ {r.get('n_linhas', 0)} cobranças de "
                     f"{r.get('n_beneficiarios', 0)} beneficiários.")

            st.write("📋 Construindo caso...")
            caso = pipeline.construir_caso_sulamerica_pme(
                caso_id=caso_id, pdfs_extracao=r,
                numero_apolice=apolice, estipulante=estipulante,
                mes_aniversario=int(mes_aniv),
                data_vigencia=vigencia, tipo_plano="coletivo_empresarial",
            )
            pipeline.salvar_caso(caso)
            st.write(f"   ✓ {len(caso.beneficiarios)} beneficiários, falso coletivo: "
                     f"{caso.contrato.falso_coletivo}")

            st.write("🧮 Calculando reajustes e aplicando correção monetária...")
            corte = date(data_acao.year - 3, data_acao.month, 1)
            saida = pipeline.executar_caso_completo(
                caso, data_distribuicao_acao=data_acao,
                indice_principal="TJSP", excluir_saidos_antes=corte,
            )
            st.session_state.saida = saida
            st.session_state.caso = caso
            status.update(label="✅ Cálculo concluído!", state="complete")
        st.rerun()
    except Exception as e:
        st.session_state.erro = str(e)
        import traceback
        st.session_state.traceback = traceback.format_exc()


# ─────────────────────── Erro ───────────────────────
if st.session_state.erro:
    st.error(f"❌ {st.session_state.erro}")
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
