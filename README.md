# advreajuste

Pipeline auditável e juridicamente defensável para cálculo de reajuste abusivo
de planos de saúde (contratos coletivos com <30 vidas — "falsos coletivos").

Alinhado a **STJ Tema 1016**, **RN 563/2022** (faixa etária), **RN 565/2022**
(pool coletivo), **Súmula 91/TJSP**, **Lei 14.905/2024** (juros legais) e
**Recomendação OAB/CFOAB 001/2024** (supervisão humana obrigatória).

## Instalação

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync
uv run advreajuste --help
```

## Uso

```bash
uv run advreajuste extrair ./data/casos/caso001/pdfs --caso caso001
uv run advreajuste calcular caso001
uv run advreajuste planilha caso001
uv run advreajuste peticao caso001
uv run advreajuste ui              # Streamlit local
```

## Deploy

Para uso real com lotes grandes de PDFs, use o deploy em VPS descrito em
`DEPLOY.md`. O Streamlit Cloud pode ficar sem memoria durante OCR de muitos
arquivos escaneados.

## Arquitetura

Ver `docs/politica_retencao.md` para LGPD. Ver módulo `calculators/` para o motor
Decimal determinístico com ROUND_HALF_EVEN em intermediários e ROUND_HALF_UP
na saída BRL.

## Aviso profissional

Esta ferramenta **não substitui supervisão humana do advogado** (Rec. OAB
001/2024). Toda peça subscrita depende de revisão e aprovação registrada
(hash + timestamp no audit log `data/custody/*.jsonl`).
