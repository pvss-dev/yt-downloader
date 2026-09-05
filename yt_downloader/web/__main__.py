import argparse

from .server import run

parser = argparse.ArgumentParser(
    prog="yt-downloader-web",
    description="Start the YouTube Downloader web interface",
)
parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
parser.add_argument("--port", type=int, default=8000, help="Port (default: 8000)")
parser.add_argument("--reload", action="store_true", help="Auto-reload on code changes")

args = parser.parse_args()
run(host=args.host, port=args.port, reload=args.reload)
