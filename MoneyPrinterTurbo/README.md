# AutoShorts Studio 🎬⚡

**AutoShorts Studio**, yapay zeka destekli video üretimi ve yayıncı yayınlarından (YouTube, Twitch, Kick) otomatik Shorts / Reels / TikTok klipleri oluşturan hepsi-bir-arada açık kaynaklı video stüdyosudur.

[![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Streamlit](https://img.shields.io/badge/Streamlit-1.59-FF4B4B?logo=streamlit&logoColor=white)](https://streamlit.io/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.136-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)

[English Documentation](README-en.md) | Türkçe Dökümantasyon

---

## 🌟 Öne Çıkan Özellikler

### 1. 🤖 Yapay Zeka ile Sıfırdan Video Üretimi
* **Gelişmiş LLM Entegrasyonu**: OpenAI (GPT-4o), Google Gemini, DeepSeek, Claude, Kimi, Grok ve yerel Ollama desteği.
* **Akıllı Stok Video Eşleştirme**: Pexels, Pixabay veya yerel video kütüphanenizden konuya en uygun sahneleri otomatik indirme ve zamanlama.
* **Doğal Seslendirme (TTS)**: Ücretsiz ve kaliteli Microsoft Edge-TTS, Kokoro, ElevenLabs, Azure Speech ve OpenAI TTS ses modelleri.
* **Animasyonlu Altyazı & Karaoke**: Kelime bazlı animasyonlar, özel fontlar, renkler ve stil efektleri.
* **Fon Müziği (BGM)**: Telifsiz fon müzikleri, otomatik ses kısma (ducking) ve ses dengeleme.
* **Çoklu Boyut Desteği**: 9:16 Dikey (Shorts / Reels / TikTok), 16:9 Yatay (YouTube) ve 1:1 Kare.

### 2. ⚡ Yayıncı Klip & Shorts Otomasyon Motoru
* **Çoklu Platform Keşfi**: YouTube Kanalları/Canlı Yayınları, Twitch VOD/Klipleri ve Kick yayınlarını otomatik izleme.
* **Tazelik & Viralite Filtresi**: Belirlenen gün eşiğinden (örn: son 7 gün) eski videoları atlama; eski videolarda yalnızca viral izlenme eşiğini aşanları kabul etme.
* **Başlık Kara Listesi**: "Açıklama", "duyuru", "sorun" gibi boş veya alakasız yayın başlıklarını otomatik eleme.
* **Hype (Tepe Noktası) Tespiti**: FFmpeg `ebur128` ses enerjisi analizi ile yayının en heyecanlı, komik ve yüksek tempolu anlarını milisaniye hassasiyetinde yakalama.
* **Intro & Outro Kırpma**: Sabit ve dinamik (sessizlik/siyah ekran) filtrelerle yayın giriş ve bitiş jeneriklerini temizleme.
* **Otomatik 9:16 Dönüştürme & Whisper Altyazı**: Yayını dikey formata odaklayıp Faster-Whisper ile renkli animasyonlu altyazılar giydirme.
* **Otomatik YouTube Shorts Yayınlama**: Günlük kota yönetimi, otomatik başlık/açıklama/etiket üretimi ve zamanlanmış paylaşım.

---

## 🖥️ Arayüz & Kullanım

AutoShorts Studio, modern ve kullanıcı dostu bir Streamlit arayüzüne sahiptir:

- **AI Video Oluşturucu Tabı**: Konunuzu yazın, sesi seçin ve tek tıkla videonuzu oluşturun.
- **Yayıncı Klip & Shorts Otomasyon Tabı**: Takip etmek istediğiniz kanalları ekleyin, filtrelerinizi belirleyin ve arkanıza yaslanın; sistem yayınları indirip en iyi klipleri kesip kanalınıza yüklesin.

---

## 🚀 Hızlı Başlangıç

### Gereksinimler
- Python 3.11+
- FFmpeg (Sistem yolunda veya `lib/ffmpeg/` altında kurulu olmalıdır)

### Kurulum

1. Repoyu klonlayın:
```bash
git clone https://github.com/muratcihanyandi/AutoShorts-Studio.git
cd AutoShorts-Studio
```

2. Sanal ortam oluşturup bağımlılıkları yükleyin:
```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/macOS
source .venv/bin/activate

pip install -r requirements.txt
```

3. WebUI'ı Başlatın:
```bash
streamlit run webui/Main.py
```
Veya Windows kullanıyorsanız kök dizindeki `start.bat` dosyasına çift tıklayarak doğrudan başlatabilirsiniz.

---

## ⚙️ Yapılandırma (`config.toml`)

İlk çalıştırmada `config.example.toml` dosyasından otomatik olarak bir `config.toml` oluşturulur.
Kullanmak istediğiniz servislerin API anahtarlarını bu dosyaya veya WebUI üzerinden Ayarlar sekmesine girebilirsiniz:

- **LLM**: OpenAI, Gemini, DeepSeek veya Kimi API anahtarları.
- **Stok Video**: Pexels / Pixabay API anahtarı.
- **YouTube Yükleyici**: `resource/client_secret.json` dosyanızı yerleştirin ve `python authenticate_youtube.py` çalıştırın.

---

## 🐳 Docker ile Çalıştırma

### CPU Sürümü
```bash
docker compose up -d
```

### GPU Hızlandırmalı Sürüm (NVIDIA CUDA)
```bash
docker build -f Dockerfile.gpu -t autoshorts-studio:gpu .
docker run --gpus all -v $(pwd)/config.toml:/AutoShortsStudio/config.toml -v $(pwd)/storage:/AutoShortsStudio/storage -p 127.0.0.1:8501:8501 autoshorts-studio:gpu
```

---

## 📄 Lisans

Bu proje [MIT Lisansı](LICENSE) altında lisanslanmıştır.
