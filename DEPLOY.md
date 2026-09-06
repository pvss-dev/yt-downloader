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

**Nada a fazer.** Não é preciso clonar o repositório nem baixar o compose: o
workflow cria a pasta e envia o `docker-compose.yml` a cada deploy. É assim que
mudanças no compose chegam ao servidor — sem clone para manter sincronizado.

A única coisa que precisa existir antes é a rede do nginx. O Compose nomeia a
rede como `<pasta>_<rede>`, então `/root/projects/nginx` com `local-net` vira
`nginx_local-net`:

```bash
docker network ls | grep local-net
```

Se o nome for outro, crie um `.env` na pasta do app na VPS:

```bash
echo "YTDL_NETWORK=o_nome_real" >> /root/projects/yt-downloader/.env
```

O `.env` fica na VPS e não é sobrescrito pelo deploy — só o
`docker-compose.yml` é enviado.

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

O workflow declara `environment: production`, então crie o environment primeiro
e coloque os secrets **dentro dele**. Assim eles só existem para deploys, não
para qualquer workflow do repositório — o que importa num repo público, onde
qualquer pessoa pode abrir um PR.

**1. Criar o environment**

`github.com/pvss-dev/yt-downloader` → **Settings** → **Environments** →
**New environment** → nome `production` → **Configure environment**

Ali dentro, opcionalmente marque *Required reviewers* com o seu usuário: cada
deploy passa a esperar a sua aprovação.

**2. Adicionar os secrets nesse environment**

A tela do environment tem **duas** listas. O que dá acesso vai em *secrets*;
o resto vai em *variables*, porque o GitHub mascara todo secret nos logs — e um
deploy que imprime `==> Updating ***` é um deploy que você não consegue depurar.

**Environment secrets** → *Add secret*:

| Secret | Valor no seu caso |
|---|---|
| `VPS_HOST` | IP ou domínio da VPS |
| `VPS_SSH_KEY` | conteúdo da chave **privada**, inteiro |
| `VPS_PORT` | só se o SSH não estiver na 22 |

**Environment variables** → *Add variable*:

| Variable | Valor no seu caso |
|---|---|
| `VPS_USER` | `root` |
| `VPS_APP_DIR` | `/root/projects/yt-downloader` |

Se algum faltar, o primeiro passo do workflow para e diz exatamente qual — em
vez de o `cd` cair no `$HOME` sem avisar.

`VPS_SSH_KEY` é o arquivo inteiro, incluindo as linhas
`-----BEGIN OPENSSH PRIVATE KEY-----` e `-----END ...-----`. A chave **pública**
correspondente precisa estar no `~/.ssh/authorized_keys` da VPS.

Gerando um par só para o deploy:

```bash
ssh-keygen -t ed25519 -C "github-deploy" -f ~/.ssh/ytdl-deploy -N ""
ssh-copy-id -i ~/.ssh/ytdl-deploy.pub root@SEU_IP
cat ~/.ssh/ytdl-deploy        # <- este conteúdo vai no VPS_SSH_KEY
```

> Não há `GHCR_TOKEN`: o pacote é público e a VPS puxa a imagem sem
> autenticação. Se algum dia tornar o pacote privado, será preciso voltar com o
> secret e um passo de `docker login` no workflow.

## 4. Deploy

Actions → **Deploy** → Run workflow → tag `latest` (o padrão).

O que o workflow faz na VPS, em ordem:

1. confere se os secrets e variables existem, e diz qual falta
2. cria a pasta na VPS, se não existir
3. envia o `docker-compose.yml` do commit sendo deployado
4. `docker pull` da imagem
5. `docker compose up -d`
6. espera o healthcheck até 5 minutos
7. se não ficar verde, imprime os últimos 50 logs e **volta para a imagem
   anterior** sozinho

> O compose enviado vem do branch em que você roda o workflow. Rodando a partir
> do `main` com a tag `latest`, compose e imagem vêm do mesmo commit.

Para voltar a uma versão específica, rode o workflow com a tag
`sha-<commit completo>`.

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
