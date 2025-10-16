# YouTube Video Downloader

Download YouTube videos with command-line interface.

## Installation

```bash
pip install -r requirements.txt
```

## Usage

```bash
# As a module
python -m yt_downloader "https://youtube.com/watch?v=VIDEO_ID"

# After installing with setup.py
pip install -e .
yt-download "https://youtube.com/watch?v=VIDEO_ID" -o ~/Downloads -v

# With custom quality
yt-download "URL" --max-quality 720 -v
```

## Project Structure

```
yt_downloader/
├── __init__.py       # Main package
├── __main__.py       # Entry point for python -m
├── cli.py            # CLI interface
├── downloader.py     # Download logic
├── config.py         # Configuration
└── exceptions.py     # Custom exceptions
```

## Features

- Download best quality video + audio
- Customizable output directory
- Quality selection (360p to 4K)
- Resume interrupted downloads
- Automatic metadata embedding
- Progress tracking
- Error handling

## Options

```
-o, --output PATH        Output directory (default: ./videos)
-v, --verbose           Show detailed information
--max-quality HEIGHT    Maximum quality: 360, 480, 720, 1080, 1440, 2160
--overwrite             Overwrite existing files
```

## Examples

```bash
# Basic download
python -m yt_downloader "https://youtube.com/watch?v=dQw4w9WgXcQ"

# Custom directory and quality
python -m yt_downloader "URL" -o ~/Videos --max-quality 720

# Verbose mode
python -m yt_downloader "URL" -v

# Overwrite existing
python -m yt_downloader "URL" --overwrite
```

## Requirements

- Python 3.8+
- yt-dlp
- ffmpeg (for merging video/audio)

## License

MIT