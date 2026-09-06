# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Builder: install Python dependencies into a virtualenv we can copy over.
# Keeping pip and its caches out of the final image saves well over a GB.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# PyTorch first, from the CPU-only index: the default wheel bundles CUDA and
# is several GB larger for no benefit on a VPS without an NVIDIA GPU.
RUN pip install --no-cache-dir \
    --index-url https://download.pytorch.org/whl/cpu \
    torch

COPY requirements.txt requirements-transcribe.txt ./
RUN pip install --no-cache-dir -r requirements-transcribe.txt

COPY setup.py README.md ./
COPY yt_downloader ./yt_downloader
RUN pip install --no-cache-dir --no-deps .


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------
FROM python:3.12-slim

# ffmpeg merges video+audio and converts to WAV for Whisper; both are required,
# not optional. curl is only here for the container healthcheck.
RUN apt-get update \
    && apt-get install --no-install-recommends -y ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

# Run unprivileged: a bug or a crafted request should not be able to touch
# anything outside the volumes mounted for it.
RUN useradd --create-home --uid 10001 app

COPY --from=builder /opt/venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Confine every client-supplied destination to this directory.
    YTDL_OUTPUT_ROOT=/data/videos \
    # Whisper caches model weights here; mount it as a volume or every
    # container restart re-downloads ~500 MB.
    XDG_CACHE_HOME=/data/cache

RUN mkdir -p /data/videos /data/cache && chown -R app:app /data

USER app
WORKDIR /data

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

# 0.0.0.0 is safe here because the container port is never published to the
# host: nginx reaches it over the internal Docker network.
CMD ["python", "-m", "yt_downloader.web", "--host", "0.0.0.0", "--port", "8000"]
