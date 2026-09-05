# YouTube Video Downloader

Baixa vídeos do YouTube pela linha de comando ou por uma interface web.

![status](https://img.shields.io/badge/tests-35%20passing-brightgreen)

## Instalação

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
```

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
└── web/
    ├── __main__.py   # `python -m yt_downloader.web`
    ├── server.py     # Rotas FastAPI + stream SSE
    ├── jobs.py       # Fila de downloads em threads
    └── static/       # index.html, style.css, app.js

tests/                # 35 testes, sem acesso à rede
```

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

## Licença

MIT
