# yt-downloader

Baixa vídeos do YouTube e transcreve áudio com Whisper, por uma interface web
ou pela linha de comando. A imagem já traz Python, ffmpeg e o Whisper — não há
nada a instalar além do Docker.

**Código, issues e documentação completa:**
https://github.com/pvss-dev/yt-downloader

## Uso

```bash
mkdir -p yt-downloader && cd yt-downloader

docker run -d --name yt-downloader \
  --user "$(id -u):$(id -g)" \
  -p 127.0.0.1:8000:8000 \
  -e YTDL_OUTPUT_ROOT=/data/videos \
  -e XDG_CACHE_HOME=/data/cache \
  -v "$PWD/videos:/data/videos" \
  -v "$PWD/.whisper-cache:/data/cache" \
  pvssdev/yt-downloader:latest
```

Abra **http://127.0.0.1:8000**. Os vídeos e transcrições aparecem em `videos/`.

Com `docker compose`, baixe o
[docker-compose.yml](https://raw.githubusercontent.com/pvss-dev/yt-downloader/main/docker-compose.yml)
e rode `docker compose up -d`.

### `--user` não é opcional

A imagem roda como um usuário sem privilégios (uid 10001), e um bind mount
preserva a dona do host. Sem passar o seu uid, o container não consegue
escrever e todo download falha com `Permission denied`.

### A porta fica em 127.0.0.1

A aplicação **não tem autenticação**. Publicá-la em `0.0.0.0` deixaria qualquer
um na sua rede enfileirar downloads e transcrições na sua máquina.

## Linha de comando

```bash
docker exec yt-downloader yt-download "URL" --max-quality 720
docker exec yt-downloader yt-transcribe /data/videos/aula.mkv --model turbo
```

## Volumes

| Caminho | Conteúdo |
|---|---|
| `/data/videos` | vídeos e transcrições — é o que você quer no host |
| `/data/cache` | pesos do Whisper (~500 MB para o `small`) |

Sem montar `/data/cache`, o modelo é baixado de novo a cada restart.

## Variáveis

| Variável | Padrão | Para quê |
|---|---|---|
| `YTDL_OUTPUT_ROOT` | — | confina a pasta de destino pedida pelo navegador |
| `XDG_CACHE_HOME` | — | onde o Whisper guarda os modelos |
| `YTDL_PUBLIC` | `0` | ignora a pasta pedida pelo cliente e aplica cota por sessão |
| `YTDL_MAX_CONCURRENT` | `3` | downloads simultâneos |
| `YTDL_MAX_TRANSCRIPTIONS` | `1` | transcrições simultâneas (CPU) |

## Tags

| Tag | O que é |
|---|---|
| `latest` | último build do `main` |
| `develop` | último build do `develop` |
| `sha-<commit>` | um commit específico, para fixar a versão |

## Requisitos

Transcrever com o modelo `small` quer ~2 GB de RAM livres. Sem GPU roda na CPU
— funciona, só mais devagar.

## Hospedar em servidor não funciona bem

O YouTube exige verificação anti-bot em requisições vindas de faixas de IP de
datacenter. Numa VPS comum, **1 de 8 vídeos funcionou** — com todos os clientes
de extração que o yt-dlp oferece. Esta imagem é feita para rodar na sua máquina.

Para transcrever em servidor, sem tocar no YouTube:
https://github.com/pvss-dev/transcribe-videos

## Licença

MIT
