# Política de retenção e tratamento de dados — advreajuste

## 1. Bases legais (LGPD)

| Dado | Base | Fundamento |
|---|---|---|
| Comuns (nome, CPF, valores) | Art. 7º VI | Exercício regular de direitos em processo judicial |
| Sensíveis (saúde) | Art. 11 II "d" | Tratamento de sensíveis em processo |
| Obrigação legal (OAB/CPC) | Art. 7º II / 11 II "a" | Guarda documental do advogado |

Não se exige consentimento do titular. Princípios mandatórios: finalidade,
necessidade, minimização, segurança, prestação de contas.

## 2. Fluxo de dados

```
PDFs originais ──SHA-256──> audit log JSONL append-only
       │
       ├─► pdfplumber/PyMuPDF (local, zero rede)
       └─► [fallback] Claude Sonnet Vision
              └─► Anthropic API (retenção 7 dias; sem treino; sem ZDR salvo enterprise)
```

## 3. Medidas técnicas

- Diretório `originals/` com permissão 0444 (read-only).
- JSONL append-only em `data/custody/<caso>.jsonl` com timestamp UTC + hash.
- Hash SHA-256 recalculado a cada leitura crítica.
- Variáveis sensíveis (`ANTHROPIC_API_KEY`) apenas em `.env` (gitignored).
- Revisão humana obrigatória registrada como evento `revisao_humana_aprovada`.

## 4. Retenção

- Originais: 5 anos após trânsito em julgado (Provimento CNJ / prazos CPC).
- Audit log: mesmo período; não pode ser editado/deletado durante vigência.
- Dados em Anthropic API: 7 dias automáticos (política comercial 14/09/2025).

## 5. Supervisão humana (Rec. OAB/CFOAB 001/2024)

A automação produz **minuta**. A advogada revisa, corrige e subscreve.
Nenhum protocolo é automático. Cláusula no contrato de honorários informa
cliente sobre uso auxiliar de IA.

## 6. RIPD — quando elaborar

Obrigatório quando: tratamento em larga escala, decisão automatizada com
impacto jurídico, ou combinação de dados sensíveis com perfilagem.
Para uso individual por caso em escritório pequeno, RIPD simplificado com
os itens (1)-(5) acima é suficiente.

## 7. Opções de residência de dados (mais restritivas → menos)

1. **Ollama local** + Llama 3.3 70B — zero rede (máxima defensabilidade).
2. **Maritaca Sabiá-4** — API no Brasil, dados descartados após resposta.
3. **AWS Bedrock sa-east-1** (Claude) — região Brasil, criptografia em trânsito/repouso.
4. **Anthropic API direta** — atual padrão do projeto; aceitável com (a) não-Claude.ai consumer, (b) cláusula informando cliente, (c) revisão humana registrada.
