from setuptools import find_packages, setup

setup(
    name="yt-downloader",
    version="2.0.0",
    packages=find_packages(exclude=["tests", "tests.*"]),
    include_package_data=True,
    package_data={"yt_downloader": ["web/static/*"]},
    install_requires=[
        "yt-dlp[default]>=2026.8.19",
    ],
    extras_require={
        "web": ["fastapi>=0.115", "uvicorn[standard]>=0.30", "python-multipart>=0.0.9"],
        # Whisper pulls in PyTorch (~1 GB, or ~200 MB for the CPU-only build
        # from https://download.pytorch.org/whl/cpu), so it stays optional.
        "transcribe": ["openai-whisper>=20240930"],
        "dev": ["pytest>=8.0", "httpx>=0.27"],
    },
    entry_points={
        "console_scripts": [
            "yt-download=yt_downloader.cli:main",
            "yt-download-web=yt_downloader.web.server:run",
            "yt-transcribe=yt_downloader.transcription.cli:main",
            "yt-download-clean=yt_downloader.cleanup_cli:main",
        ],
    },
    python_requires=">=3.10",
    author="Paulo Vitor S. Soares",
    description="YouTube downloader and Whisper transcriber, with a CLI and a web interface",
    keywords="youtube download video yt-dlp whisper transcription subtitles",
)
