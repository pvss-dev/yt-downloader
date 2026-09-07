# YouTube Downloader & Transcriptor

Baixa vídeos do YouTube e transcreve áudio com Whisper — pela linha de comando
ou por uma interface web.

![tests](https://img.shields.io/badge/tests-107%20passing-brightgreen)
[![docker](https://img.shields.io/badge/docker-pvssdev%2Fyt--downloader-blue)](https://hub.docker.com/r/pvssdev/yt-downloader)

Há dois jeitos de usar: **puxar a imagem Docker** (nada a instalar além do
Docker) ou **clonar e rodar com Python**. Os dois estão abaixo.

## Opção 1 — Docker

Nada a instalar além do Docker: a imagem já traz Python, ffmpeg e o Whisper.

```bash
mkdir -p yt-downloader && cd yt-downloader
curl -O https://raw.githubusercontent.com/pvss-dev/yt-downloader/main/docker-compose.yml
docker compose up -d
```

Abra **http://127.0.0.1:8000**. Os vídeos e transcrições aparecem na pasta
`videos/`, ao lado do compose.

Ou sem o compose, num comando só:

```bash
docker run -d --name yt-downloader \
  --user "$(id -u):$(id -g)" \
  -p 127.0.0.1:8000:8000 \
  -e YTDL_OUTPUT_ROOT=/data/videos \
  -e XDG_CACHE_HOME=/data/cache \
  -v "$PWD/videos:/data/videos" \
  -v "$PWD/.whisper-cache:/data/cache" \
  pvssdev/yt-downloader:latest
```

> **O `--user` não é opcional.** A imagem roda como um usuário sem privilégios,
> e um bind mount preserva a dona do host — sem passar o seu uid, o container
> não consegue escrever e todo download falha com `Permission denied`. O
> `docker-compose.yml` já cuida disso; se o seu usuário não for 1000, crie um
> `.env` ao lado com `UID=` e `GID=` (veja `id -u` e `id -g`).

A porta é publicada apenas em `127.0.0.1`: a aplicação não tem autenticação, e
expô-la na rede deixaria qualquer um enfileirar downloads na sua máquina.

Também dá para usar a CLI dentro do container:

```bash
docker compose exec yt-downloader yt-download "URL" --max-quality 720
```

### Tags disponíveis

| Tag | O que é |
|---|---|
| `latest` | último build do `main` |
| `develop` | último build do `develop` |
| `sha-<commit>` | um commit específico, para fixar a versão |

### Limpeza dos volumes

```bash
docker compose down                 # para, mantendo videos/ e o cache
rm -rf .whisper-cache               # apaga os modelos baixados (~500 MB)
```

## Opção 2 — Clonar e rodar com Python

O venv é obrigatório, não opcional. Ative-o **antes** do `pip install`:

```bash
python3 -m venv .venv
source .venv/bin/activate        # Linux/macOS
# .venv\Scripts\activate         # Windows

pip install -r requirements.txt
```

> Se aparecer `error: externally-managed-environment`, o `pip` está rodando no
> Python do sistema em vez do venv (Debian/Ubuntu bloqueiam isso — PEP 668).
> Rode `source .venv/bin/activate` e tente de novo; `which python` deve apontar
> para dentro de `.venv/`. Não use `--break-system-packages`: isso instala o
> yt-dlp por cima do Python do sistema e pode quebrar pacotes do próprio SO.
>
> Alternativa sem ativar nada — chame o binário do venv direto:
>
> ```bash
> .venv/bin/pip install -r requirements.txt
> .venv/bin/python -m yt_downloader.web
> ```

Requer **ffmpeg** instalado no sistema (usado para juntar vídeo + áudio e converter para mp3):

```bash
sudo apt install ffmpeg     # Debian/Ubuntu
brew install ffmpeg         # macOS
```

### Transcrição (opcional)

A transcrição usa o Whisper, que puxa o PyTorch junto. Fica fora da instalação
padrão porque quem só quer baixar vídeo não precisa de ~1 GB de dependências.

```bash
# Sem GPU NVIDIA? Instale o PyTorch CPU-only primeiro (~200 MB em vez de ~1 GB)
pip install torch --index-url https://download.pytorch.org/whl/cpu

pip install -r requirements-transcribe.txt
```

Sem esse extra, tudo o mais funciona normalmente: o CLI avisa como instalar e a
interface web mostra a transcrição como indisponível em vez de falhar.

> **Mantenha o yt-dlp atualizado.** O YouTube muda o player com frequência e uma
> versão de alguns meses atrás começa a falhar com `HTTP Error 403: Forbidden`
> na hora de baixar a mídia — mesmo que a leitura dos metadados ainda funcione.
> Quando um download falhar, o primeiro passo é sempre:
>
> ```bash
> pip install -U "yt-dlp[default]"
> ```

## Interface web

```bash
python -m yt_downloader.web              # http://127.0.0.1:8000
python -m yt_downloader.web --port 3000
```

Cole a URL e a página mostra um card com título, canal, duração e thumbnail
antes de baixar. Durante o download há barra de progresso em tempo real
(velocidade, ETA, bytes), botão de cancelar e link para salvar o arquivo.
Vários downloads rodam em paralelo.

O progresso chega ao navegador por Server-Sent Events, então a página pode ser
recarregada sem perder os downloads em andamento.

### Transcrever pela interface

O toggle **Transcrever o áudio** fica logo abaixo do campo de URL, sempre
visível. Ao ligá-lo aparecem os seletores de modelo Whisper e de idioma (ou
"detectar idioma"). O card ganha as etapas de carregar modelo e transcrever,
mostra um trecho do texto ao terminar e um botão para baixar o `.txt`.

A transcrição roda sobre o arquivo já baixado, então ligar o toggle custa **um**
download, não dois.

### Transcrever um arquivo do seu computador

Arraste um vídeo ou áudio para qualquer lugar da página — ou clique em
**escolha um arquivo**. O arquivo é enviado ao servidor local, transcrito e a
cópia no servidor é apagada em seguida; fica só o `.txt`, salvo na pasta de
destino configurada em Opções.

Como o envio é por HTTP, isso funciona mesmo com o navegador em outra máquina da
rede. Limite de 4 GB por arquivo.

### Tema claro / escuro

O botão no canto superior direito alterna entre os dois temas. A escolha fica
no `localStorage` do navegador e sobrevive ao reload. O padrão é escuro.

## Linha de comando

```bash
# Básico
python -m yt_downloader "https://youtube.com/watch?v=VIDEO_ID"

# Depois de instalar com `pip install -e .`
yt-download "URL" -o ~/Downloads --max-quality 720

# Só os metadados, sem baixar
yt-download "URL" --info

# Extrair áudio
yt-download "URL" --audio-only --audio-format mp3

# Playlist inteira
yt-download "https://youtube.com/playlist?list=ID" --playlist

# Baixar e transcrever numa tacada só
yt-download "URL" --transcribe --whisper-model turbo --language pt
```

### Transcrever

```bash
# Arquivo local
yt-transcribe aula.mp4
yt-transcribe aula.mp4 transcricao.txt

# Direto de uma URL (baixa só o áudio, num diretório temporário)
yt-transcribe "https://youtube.com/watch?v=VIDEO_ID" --model turbo

# Legendas .srt junto
yt-transcribe podcast.mp3 --language en --srt

# Deixar o Whisper descobrir o idioma
yt-transcribe entrevista.m4a --detect-language
```

| Modelo   | VRAM   | Velocidade | Quando usar                    |
|----------|--------|------------|--------------------------------|
| `tiny`   | ~1 GB  | ~10x       | testes rápidos                 |
| `base`   | ~1 GB  | ~7x        | processamento rápido           |
| `small`  | ~2 GB  | ~4x        | **padrão, bom equilíbrio**     |
| `medium` | ~5 GB  | ~2x        | alta qualidade                 |
| `large`  | ~10 GB | 1x         | melhor qualidade               |
| `turbo`  | ~6 GB  | ~8x        | **rápido e quase tão bom**     |

`turbo` não faz tradução (`--translate`); para isso use `medium` ou `large`.

### Opções

```
-o, --output PATH       Pasta de destino (padrão: ./videos)
-v, --verbose           Log detalhado
--max-quality HEIGHT    Qualidade máxima: 360, 480, 720, 1080, 1440, 2160
--overwrite             Sobrescreve arquivos existentes
--audio-only            Extrai apenas o áudio
--audio-format FMT      mp3 (padrão), m4a, opus, flac, wav
--playlist              Baixa a playlist inteira
--subtitles             Baixa e embute legendas (pt, en)
--thumbnail             Embute a thumbnail como capa
--info                  Só imprime as informações do vídeo

--transcribe            Transcreve o áudio depois de baixar
--whisper-model MODEL   tiny, base, small (padrão), medium, large, turbo
--language LANG         Idioma falado, ex. pt, en (padrão: pt)
--detect-language       Deixa o Whisper detectar o idioma
--srt                   Com --transcribe, gera também um .srt
```

Sem `--playlist`, uma URL do tipo `/watch?v=X&list=Y` baixa **apenas o vídeo X**.

## Uso como biblioteca

```python
from yt_downloader import VideoDownloader, DownloaderConfig

config = DownloaderConfig(max_height=720, audio_only=False)
downloader = VideoDownloader(output_path="~/Videos", config=config)

# Metadados sem baixar
info = downloader.get_info(url)
print(info.title, info.duration_display, info.available_heights)

# Download com callback de progresso
def on_progress(p):
    if p.overall_percent is not None:
        print(f"{p.overall_percent:.1f}%")

downloader = VideoDownloader(config=config, on_progress=on_progress)
result = downloader.download(url)

if result.success:
    print("Salvo em", result.filepath)
else:
    print("Falhou:", result.error)
```

`downloader.cancel()` interrompe um download em andamento (de outra thread);
o arquivo `.part` fica no disco e o download é retomado na próxima tentativa.

## Estrutura

```
yt_downloader/
├── __init__.py       # API pública do pacote
├── __main__.py       # Entry point do `python -m yt_downloader`
├── cli.py            # Interface de linha de comando
├── downloader.py     # VideoDownloader, VideoInfo, Progress, DownloadResult
├── config.py         # DownloaderConfig -> opções do yt-dlp
├── exceptions.py     # Exceções do domínio
├── web/
│   ├── __main__.py   # `python -m yt_downloader.web`
│   ├── server.py     # Rotas FastAPI + stream SSE
│   ├── jobs.py       # Fila de downloads/transcrições em threads
│   └── static/       # index.html, style.css, app.js
└── transcription/
    ├── cli.py        # Comando `yt-transcribe`
    ├── config.py     # TranscriptionConfig, modelos do Whisper
    ├── converter.py  # Conversão para WAV 16 kHz mono via ffmpeg
    ├── transcriber.py # Whisper + progresso
    └── service.py    # Orquestra download -> conversão -> transcrição

tests/                # 107 testes, sem acesso à rede
```

O transcriptor **não tem downloader próprio**: ele usa o `VideoDownloader` do
pacote em modo áudio. Assim existe um único ponto para manter atualizado quando
o YouTube muda.

## Limpeza automática

Nada é apagado por padrão. A limpeza só roda quando você define pelo menos um
limite — e `--dry-run` mostra o que sairia antes de qualquer coisa sair.

```bash
# Ver o que uma regra de 30 dias removeria, sem remover
yt-download-clean --days 30 --dry-run

# Aplicar
yt-download-clean --days 30

# Outros limites (combináveis)
yt-download-clean --max-gb 50      # mantém a pasta abaixo de 50 GB
yt-download-clean --keep 100       # mantém só os 100 arquivos mais novos
```

No cron, para um servidor sem supervisão:

```cron
0 4 * * *  /caminho/.venv/bin/yt-download-clean --days 30 -o /srv/videos
```

Ou deixe o próprio servidor varrer de hora em hora:

```bash
python -m yt_downloader.web --retention-days 30
python -m yt_downloader.web --retention-max-gb 50 --retention-interval 30
```

O que a limpeza **nunca** toca:

- arquivos que não são deste projeto (`.docx`, `.jpg`, `.env`, código…) — só
  extensões de mídia e, se você pedir, `.txt`/`.srt`
- transcrições, a menos que você passe `--with-transcripts` (são pequenas e caras
  de refazer)
- symlinks — nunca são seguidos, para não apagar algo fora da pasta
- arquivos de downloads em andamento

Downloads interrompidos (`.part`) com mais de 24h são removidos junto, já que
nada vai retomá-los. Use `--keep-partials` para preservá-los.

## Hospedar em servidor

Este projeto é feito para rodar **na sua própria máquina**, e é assim que
recomendo usá-lo.

Hospedar em VPS esbarra num problema que não é do código: o YouTube exige
verificação anti-bot em requisições vindas de faixas de IP de datacenter. Medi
numa VPS comum — **1 de 8 vídeos funcionou**, com todos os clientes de extração
que o yt-dlp oferece. Só contorna com cookies de uma conta logada, o que expõe
essa conta a suspensão.

Se ainda assim for hospedar, dois cuidados mínimos: a aplicação **não tem
autenticação** (quem alcança a porta usa), e defina `YTDL_OUTPUT_ROOT` para
confinar a pasta de destino que o cliente pede.

Para transcrever em servidor, sem tocar no YouTube, veja
[transcribe-videos](https://github.com/pvss-dev/transcribe-videos) — é a parte
de transcrição deste projeto, isolada e sem o problema de IP.

## Testes

```bash
pip install -r requirements-dev.txt
pytest -q
```

A suíte não acessa a rede: as chamadas ao yt-dlp são substituídas por um duplo
de teste, então ela roda offline e não quebra quando o YouTube muda.

## Requisitos

- Python 3.10+
- yt-dlp (atualizado)
- ffmpeg
- Opcional, para transcrever: openai-whisper + PyTorch

## Licença

MIT
