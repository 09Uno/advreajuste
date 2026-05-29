# Deploy em VPS com subdominio

Este e o caminho recomendado para producao. O app roda em Docker, o Caddy fica na frente fazendo HTTPS automatico, e os dados/cache ficam em volume persistente.

## Estrutura

- `Dockerfile`: imagem do app com Streamlit, Tesseract OCR em portugues e Poppler.
- `deploy/vps/compose.yaml`: sobe o app e o Caddy.
- `deploy/vps/Caddyfile`: reverse proxy com HTTPS automatico.
- `deploy/vps/.env.example`: variaveis para dominio, senha e limites de OCR.

## VPS recomendada

Para a rotina com 100+ PDFs:

- Ubuntu 22.04 ou 24.04.
- 4 GB RAM como ponto de partida.
- 2 vCPU ou mais.
- 80 GB de disco ou mais se forem guardar muitos casos.

Se muitos PDFs forem escaneados, priorize RAM. OCR transforma paginas em imagens e isso consome memoria.

## 1. Apontar o subdominio

No painel DNS do dominio, crie:

```text
Tipo: A
Nome: calculadora
Valor: IP_DA_VPS
TTL: automatico
```

Exemplo final:

```text
calculadora.seudominio.com.br
```

Aguarde a propagacao antes de subir o Caddy. Normalmente e rapido, mas pode levar alguns minutos.

## 2. Preparar a VPS

Entre por SSH:

```bash
ssh root@IP_DA_VPS
```

Instale Docker, Git e firewall:

```bash
apt-get update
apt-get install -y ca-certificates curl git ufw
curl -fsSL https://get.docker.com | sh
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
```

## 3. Baixar o projeto

```bash
mkdir -p /opt
cd /opt
git clone https://github.com/laurazzopelaro/advreajuste.git
cd /opt/advreajuste/deploy/vps
```

Se o repositorio estiver privado, gere um token no GitHub ou clone usando SSH.

## 4. Configurar ambiente

```bash
cp .env.example .env
nano .env
```

Edite pelo menos:

```bash
APP_DOMAIN=calculadora.seudominio.com.br
APP_PASSWORD=sua_senha_forte
```

Para o primeiro deploy, mantenha:

```bash
ADVREAJUSTE_PDF_WORKERS=2
ADVREAJUSTE_OCR_WORKERS=1
ADVREAJUSTE_OCR_DPI=150
STREAMLIT_SERVER_MAX_UPLOAD_SIZE=2048
```

Depois, se a VPS estiver folgada de RAM, teste `ADVREAJUSTE_PDF_WORKERS=4`. Aumente `ADVREAJUSTE_OCR_WORKERS` com cuidado.

## 5. Subir o app

Na pasta `/opt/advreajuste/deploy/vps`:

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f app
```

Quando o Caddy conseguir emitir o certificado, o app abre em:

```text
https://calculadora.seudominio.com.br
```

## 6. Atualizar depois

```bash
cd /opt/advreajuste
git pull
cd deploy/vps
docker compose up -d --build
docker compose logs -f app
```

## 7. Backup dos dados

O volume persistente do app chama `advreajuste_data`.

Backup:

```bash
mkdir -p /opt/backups
docker run --rm \
  -v advreajuste_data:/data:ro \
  -v /opt/backups:/backup \
  alpine tar czf /backup/advreajuste-data-$(date +%F).tar.gz -C /data .
```

Restauracao:

```bash
docker run --rm \
  -v advreajuste_data:/data \
  -v /opt/backups:/backup \
  alpine sh -c 'cd /data && tar xzf /backup/advreajuste-data-AAAA-MM-DD.tar.gz'
```

## Diagnostico rapido

Ver containers:

```bash
docker compose ps
```

Logs do app:

```bash
docker compose logs -f app
```

Logs do proxy/HTTPS:

```bash
docker compose logs -f caddy
```

Reiniciar tudo:

```bash
docker compose restart
```

## Por que VPS

VPS resolve o problema que apareceu no Streamlit Cloud: lote grande + OCR pode derrubar processo pequeno. Aqui voce controla RAM, CPU, disco, dominio, HTTPS e limites de upload.
