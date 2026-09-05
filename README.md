# YouTube Downloader & Transcriptor

Baixa vídeos do YouTube e transcreve áudio com Whisper — pela linha de comando
ou por uma interface web.

![status](https://img.shields.io/badge/tests-57%20passing-brightgreen)

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

tests/                # 57 testes, sem acesso à rede
```

O transcriptor **não tem downloader próprio**: ele usa o `VideoDownloader` do
pacote em modo áudio. Assim existe um único ponto para manter atualizado quando
o YouTube muda.

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
