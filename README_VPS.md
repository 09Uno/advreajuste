# Pacote VPS AdvReajuste

Esta pasta contem o pacote limpo para subir na VPS, sem `.git`, sem logs e sem dados de clientes.

## Arquivos principais

- `compose.yaml`: deploy com Caddy, recomendado quando houver subdominio apontado para a VPS.
- `compose.ip.yaml`: deploy temporario por IP, sem HTTPS.
- `Dockerfile`: imagem do app.
- `Caddyfile`: proxy HTTPS automatico para subdominio.
- `.env.example`: copie para `.env` e edite senha/dominio.
- `setup_ubuntu.sh`: prepara Ubuntu com Docker e firewall.

## Modo recomendado: subdominio com HTTPS

1. Aponte um registro DNS `A` para o IP da VPS.
2. Na VPS, copie esta pasta para `/opt/advreajuste`.
3. Rode:

```bash
cd /opt/advreajuste
cp .env.example .env
nano .env
docker compose up -d --build
docker compose logs -f
```

No `.env`, ajuste:

```bash
APP_DOMAIN=calculadora.seudominio.com.br
APP_PASSWORD=sua_senha_forte
```

## Modo temporario: acesso por IP

Use apenas para teste inicial:

```bash
cd /opt/advreajuste
cp .env.example .env
nano .env
docker compose -f compose.ip.yaml up -d --build
```

Abra:

```text
http://IP_DA_VPS:8501
```

Para esse modo, libere a porta:

```bash
ufw allow 8501/tcp
```

Depois que houver subdominio, volte para `compose.yaml` com Caddy e feche a porta 8501.
