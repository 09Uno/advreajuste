# Distribuição para mentorados

## Fluxo de entrega

```
[Laura] build_portable.ps1 → [calculadora-reajuste-v0.1.0.zip ~200MB]
            ↓  (email/WeTransfer/Drive)
[Mentorado] extrai ZIP → clica "Iniciar Calculadora.bat" → navegador abre
```

## Como gerar o pacote

Na sua máquina (Windows PowerShell):

```powershell
cd C:\Users\laura\OneDrive\ARQUIVOS GERAIS\MENTORIA\advreajuste
.\scripts\build_portable.ps1 -Version "0.1.0"
```

O script faz:
1. Baixa Python 3.12 embeddable (~12 MB) se não tiver em `dist_portable/python/`
2. Instala todas as dependências nesse Python isolado
3. Copia `src/` da aplicação
4. Copia `.env.example`, `docs/`, cria pastas `data/`, `logs/`
5. Gera `calculadora-reajuste-v0.1.0.zip` (~200 MB)

Tempo: ~5 minutos na primeira vez (pip install); ~30s nas próximas (só copia
código-fonte e reempacota).

## Conteúdo do ZIP

```
calculadora-reajuste-v0.1.0/
├── Iniciar Calculadora.bat          ← 2 cliques aqui
├── LEIA-ME.txt                      ← instruções simples
├── MANUAL.html                      ← manual completo
├── .env.example                     ← configurações opcionais
├── python/                          ← Python embeddable (30 MB)
├── src/advreajuste/                 ← código (4 MB)
├── data/                            ← vazio, casos ficam aqui
├── logs/                            ← vazio, logs ficam aqui
└── docs/
    ├── politica_retencao.md         ← LGPD
    └── clausula_honorarios.md       ← cláusula-modelo
```

## Checklist antes de enviar

- [ ] Rodar `pytest tests/unit` — todos passando
- [ ] Rodar `scripts/caso_grigio.py` — caso de referência OK
- [ ] Testar o ZIP numa máquina limpa (VM ou colega)
- [ ] Verificar que `Iniciar Calculadora.bat` abre o navegador
- [ ] Conferir que nenhuma chave de API real está no `.env.example`
- [ ] Testar upload de um PDF, cálculo e download da planilha

## Opções de envio

| Canal | Prós | Contras |
|---|---|---|
| **Google Drive** (link pasta) | gratuito, fácil | expõe tamanho/histórico |
| **WeTransfer** | link expira em 7d | limite 2GB free |
| **Email** | direto | 25MB limite (não cabe) |
| **USB/Pendrive** | presencial, 100% offline | logística |

Recomendo **Google Drive com acesso restrito** a e-mails específicos dos
mentorandos autorizados.

## Cláusula de uso restrito (para incluir no email)

> Este software é de uso exclusivo dos mentorandos do programa de Laura
> Zopelaro Advocacia. Não pode ser redistribuído, comercializado ou publicado
> em repositórios públicos sem autorização expressa. Dados de clientes devem
> ser processados localmente (sem enviar a servidores externos) salvo quando
> houver cláusula contratual específica nesse sentido. Responsabilidade
> profissional pela peça protocolada é integralmente do advogado signatário
> (Rec. OAB/CFOAB 001/2024).

## Atualização (enviar nova versão)

Quando houver correção ou melhoria:

```powershell
# Bump da versão
.\scripts\build_portable.ps1 -Version "0.2.0"
```

Enviar novo ZIP. O mentorado extrai sobre a pasta existente — os **casos em
`data/` são preservados** (ou ele extrai em pasta nova e move o `data/` antigo).

## Roadmap de distribuição

### v1 (atual) — ZIP portátil
- Instalação: extrair + clicar
- Update: receber novo ZIP

### v2 (próximo) — Instalador Inno Setup
- `.exe` de ~100 MB com "Next-Next-Finish"
- Atalho no menu Iniciar e desktop
- Desinstalador no Painel de Controle
- Auto-update opcional (opcional v3)

### v3 (futuro) — Multi-plataforma
- Versão Mac (.app via py2app)
- Versão Linux (.AppImage ou pacote .deb)
- Sincronização opcional entre dispositivos
