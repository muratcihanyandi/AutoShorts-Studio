# AutoShorts Studio 🎬⚡

**AutoShorts Studio** is an all-in-one AI video creation and streamer clip automation platform that turns ideas into finished videos and cuts viral Shorts/Reels/TikToks from YouTube, Twitch, and Kick streams automatically.

[![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Streamlit](https://img.shields.io/badge/Streamlit-1.59-FF4B4B?logo=streamlit&logoColor=white)](https://streamlit.io/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.136-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)

English Documentation | [Türkçe Dökümantasyon](README.md)

---

## 🌟 Key Features

### 1. 🤖 AI Video Generator from Scratch
* **Multi-LLM Engine**: Seamlessly switch between OpenAI (GPT-4o), Google Gemini, DeepSeek, Claude, Kimi, Grok, and local Ollama.
* **Smart Stock Footage Matching**: Automatically downloads, scores, and trims relevant HD footage from Pexels, Pixabay, or your local asset library.
* **Natural Voice Synthesis (TTS)**: Built-in support for Microsoft Edge-TTS (free, no key required), Kokoro, ElevenLabs, Azure Speech, and OpenAI Audio.
* **Dynamic Animated Subtitles**: Word-level karaoke captions, customizable fonts, colors, stroke effects, and shadow styling.
* **Smart Background Music (BGM)**: Royalty-free music library with automatic audio ducking and loudness balancing.
* **Multi-Aspect Ratio**: 9:16 Vertical (Shorts/Reels/TikTok), 16:9 Landscape (YouTube), and 1:1 Square.

### 2. ⚡ Streamer Clips & Auto-Shorts Automation
* **Multi-Platform Scraper**: Monitor YouTube channels, Twitch VODs/Clips, and Kick live streams.
* **Freshness & Virality Filtration**: Filter out videos older than a target age threshold (e.g., 7 days) unless they surpass viral view benchmarks.
* **Title Blacklist**: Automatically exclude non-content videos (e.g., announcements, technical issue notices, updates).
* **Audio Energy / Hype Peak Detection**: Sub-second accurate spike detection using FFmpeg `ebur128` integrated loudness to isolate peak moments.
* **Smart Intro & Outro Trimming**: Automatically strip stream countdowns, intro animations, and end cards.
* **9:16 Vertical Cropping & Whisper Captions**: Automatic vertical face/action framing paired with Faster-Whisper karaoke subtitle generation.
* **Automated YouTube Shorts Publishing**: Built-in scheduling, daily quota limits, automated metadata generation, and direct YouTube upload.

---

## 🚀 Quick Start

### Requirements
- Python 3.11+
- FFmpeg installed in system PATH or available in `lib/ffmpeg/`

### Installation

1. Clone the repository:
```bash
git clone https://github.com/muratcihanyandi/AutoShorts-Studio.git
cd AutoShorts-Studio
```

2. Set up virtual environment & dependencies:
```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/macOS
source .venv/bin/activate

pip install -r requirements.txt
```

3. Launch WebUI:
```bash
streamlit run webui/Main.py
```
Or on Windows, simply double-click `start.bat`.

---

## ⚙️ Configuration (`config.toml`)

On the first launch, `config.toml` is created from `config.example.toml`. You can configure your credentials via the WebUI or directly in `config.toml`:

- **LLM**: API key for OpenAI, Gemini, DeepSeek, or Kimi.
- **Stock Footage**: Pexels / Pixabay API key.
- **YouTube Uploader**: Place your OAuth `client_secret.json` in `resource/` and run `python authenticate_youtube.py`.

---

## 🐳 Docker Support

### CPU Mode
```bash
docker compose up -d
```

### GPU Mode (NVIDIA CUDA)
```bash
docker build -f Dockerfile.gpu -t autoshorts-studio:gpu .
docker run --gpus all -v $(pwd)/config.toml:/AutoShortsStudio/config.toml -v $(pwd)/storage:/AutoShortsStudio/storage -p 127.0.0.1:8501:8501 autoshorts-studio:gpu
```

---

## 📄 License

Distributed under the [MIT License](LICENSE).
