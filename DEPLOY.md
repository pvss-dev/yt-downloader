# Deploy na VPS

Pipeline: o GitHub Actions roda os testes, builda a imagem e publica no GitHub
Container Registry. Um segundo workflow, **manual**, conecta na VPS por SSH,
baixa a imagem e reinicia o container. O nginx que já existe faz o proxy.

O deploy é manual de propósito: reiniciar o container mata download e
transcrição em andamento, então a hora é você quem escolhe.

```
push → CI (testes + build + push ghcr.io)
                                   ↓
                    workflow_dispatch → SSH → docker pull + up -d → healthcheck
                                                                        ↓ falhou
                                                                    rollback
```

## Acesso aberto: o que segura a VPS

A aplicação fica pública, sem login. O que impede que ela derrube a máquina:

| Camada | Onde | O que faz |
|---|---|---|
| Rate limit | nginx | 6 jobs/min por IP, 30 req/min no preview, 12 conexões |
| Cota por sessão | app | 20 jobs/hora por visitante (`YTDL_JOBS_PER_HOUR`) |
| Concorrência | app | 3 jobs simultâneos, **1 transcrição** por vez |
| Pasta fixa | app | `YTDL_PUBLIC=1` ignora a pasta pedida pelo cliente |
| Retenção | app | apaga por idade e cota de disco |

Cada visitante enxerga só os próprios downloads — o app separa por cookie de
sessão. Isso **não é autenticação**: não impede ninguém de usar, só impede um
visitante de ver, cancelar ou baixar o trabalho de outro.

A porta do container **não é publicada** no host. O nginx é a única entrada.

> Hospedar um downloader do YouTube aberto ao público tem implicação de direito
> autoral e contraria os termos do YouTube; o IP da VPS também tende a ser
> limitado pelo volume de requisições. É a sua chamada — está registrado aqui
> porque é parte do quadro.

## 1. Na VPS

```bash
# Pasta do app (só o compose vive aqui; o código vem na imagem)
mkdir -p /opt/yt-downloader && cd /opt/yt-downloader
curl -O https://raw.githubusercontent.com/pvss-dev/yt-downloader/main/docker-compose.yml
```

A rede do compose do nginx precisa existir antes. O Compose nomeia a rede como
`<pasta>_<rede>`, então a pasta `nginx/` com a rede `local-net` vira
`nginx_local-net`. Confira:

```bash
docker network ls | grep local-net
```

Se o nome for outro, aponte no `.env` desta pasta:

```bash
echo "YTDL_NETWORK=o_nome_real" >> /opt/yt-downloader/.env
```

## 2. No projeto do nginx

Copie `deploy/nginx/yt-downloader.conf` para `conf.d/` e ajuste o
`server_name`. O arquivo segue o mesmo padrão do seu `default.conf`: bloco HTTP
para o desafio ACME mais redirect, e bloco HTTPS com o certificado.

**A ordem importa.** O bloco HTTPS aponta para um certificado que ainda não
existe, e o nginx se recusa a subir sem ele — derrubando junto o `pvss.dev.br`.

```bash
# 1. Aponte yt.pvss.dev.br (A/AAAA) para o IP da VPS
# 2. Comente o bloco `server { listen 443 ... }` do arquivo
docker exec nginx nginx -s reload

# 3. Emita o certificado
docker compose run --rm certbot certonly --webroot \
  -w /var/www/certbot -d yt.pvss.dev.br

# 4. Descomente o bloco HTTPS
docker exec nginx nginx -t && docker exec nginx nginx -s reload
```

O certbot que já roda no seu compose renova esse certificado junto com os
demais, sem configuração extra.

## 3. Secrets no GitHub

Em **Settings → Secrets and variables → Actions**:

| Secret | O que é |
|---|---|
| `VPS_HOST` | IP ou domínio da VPS |
| `VPS_USER` | usuário do SSH |
| `VPS_SSH_KEY` | chave **privada** (a pública vai no `authorized_keys` da VPS) |
| `VPS_PORT` | porta do SSH, se não for 22 |
| `VPS_APP_DIR` | `/opt/yt-downloader` |
| `GHCR_TOKEN` | Personal Access Token com escopo `read:packages` |

O `GHCR_TOKEN` é necessário porque a VPS puxa a imagem por conta própria; o
`GITHUB_TOKEN` automático só vale dentro do runner.

Crie também o environment **production** (Settings → Environments) se quiser
exigir aprovação antes de cada deploy.

## 4. Deploy

Actions → **Deploy** → Run workflow → escolha a tag (`latest`, ou o SHA
completo de um commit para voltar a uma versão específica).

O workflow espera o healthcheck ficar verde. Se não ficar em 5 minutos, ele
imprime os logs e **volta para a imagem anterior** sozinho.

## Volumes e limpeza

| Volume | Conteúdo |
|---|---|
| `ytdl-media` | vídeos e transcrições |
| `ytdl-cache` | pesos do Whisper (~500 MB para o `small`) |

O cache é volume por um motivo prático: sem ele, todo restart do container
rebaixa meio giga de modelo.

A limpeza automática vem ligada no compose — 30 dias ou 20 GB, o que vier
primeiro. Ajuste no `.env`:

```bash
YTDL_RETENTION_DAYS=15
YTDL_RETENTION_MAX_GB=10
YTDL_MEMORY_LIMIT=4g
```

## Dimensionamento

- **Disco**: a imagem ocupa **3,7 GB** (PyTorch e ffmpeg dominam), mais ~500 MB
  do cache do modelo, mais os vídeos. Reserve pelo menos 10 GB além da cota de
  retenção. O primeiro `docker pull` na VPS baixa esses 3,7 GB.
- **RAM**: o modelo `small` na CPU quer ~2 GB livres durante a transcrição. O
  compose limita o container a 4 GB para que uma transcrição não jogue a VPS
  inteira em swap. Numa VPS de 1 GB a transcrição não roda.
- **CPU**: transcrever é o gargalo. Sem GPU, conte alguns minutos de CPU cheia
  por hora de áudio.

## Operação

```bash
docker compose logs -f yt-downloader     # acompanhar
docker compose ps                        # estado e saúde
docker compose down                      # parar (volumes ficam)

# Voltar a uma versão anterior sem esperar build:
YTDL_IMAGE=ghcr.io/pvss-dev/yt-downloader:<sha> docker compose up -d
```

## Limites conhecidos

- **A cota é por cookie, não por pessoa.** Limpar os cookies zera a contagem.
  O rate limit do nginx, esse é por IP, e é o que segura abuso deliberado.
- **Sessões não expiram sozinhas.** O cookie dura 30 dias; a lista de jobs em
  memória é limitada a 200 e some quando o container reinicia.
- **Não há fila visível.** Passando de 3 jobs simultâneos, o job espera até
  30 minutos por uma vaga e depois falha com "servidor ocupado".
