from setuptools import setup, find_packages

setup(
    name="yt-downloader",
    version="1.0.0",
    packages=find_packages(),
    install_requires=[
        "yt-dlp>=2023.0.0",
    ],
    entry_points={
        "console_scripts": [
            "yt-download=yt_downloader.cli:main",
        ],
    },
    python_requires=">=3.8",
    author="Your Name",
    description="YouTube video downloader using yt-dlp",
    keywords="youtube download video yt-dlp",
)
