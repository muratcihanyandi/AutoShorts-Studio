# OpenShorts Klip Motoru Entegrasyonu Implementation Plan

> **For Claude / Antigravity:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** OpenShorts projesinin açık kaynaklı yapay zeka klip çıkarma yeteneklerini (Facecam & Gameplay tespiti, LLM ile viral an seçimi, kanca/hook görseli giydirme, yayıncı & fan kanalları taraması) MoneyPrinterTurbo projesinin mevcut mimarisini bozmadan entegre etmek ve kusursuz çalıştığını testlerle doğrulamak.

**Architecture:**
1. **Veri Modeli (`clip_schema.py`):** `ClipLayoutType.STREAMER_FACECAM` şablonu, `hook_text`, `viral_score` ve `fan_channel_queries` alanları eklenir.
2. **Viral An & Kanca Seçici (`hype_detector.py`):** OpenShorts'un transkript zaman pencereli LLM analiz mantığı `llm.py` üzerinden entegre edilir. Metindeki en viral 15-60 saniyelik aralık, kanca başlığı (`hook_text`) ve YouTube başlığı belirlenir.
3. **Kanca Grafiği & Yayıncı Yerleşimi (`clip_editor.py`):**
   - `camera_inset` algoritması: 16:9 ekranda yayıncının facecam kutusu (sol-alt, sağ-alt, sol-üst vb.) tespit edilir.
   - `streamer_facecam` 9:16 layout: Üste facecam, alta oyun ekranı veya odaklı içerik bindirilir.
   - PIL (Pillow) ile yüksek kontrastlı, modern kanca (hook) çıkartması üretilir ve videonun üstüne FFmpeg ile bindirilir.
4. **Çoklu Kaynak & Fan Kanalları (`clip_downloader.py`):** Yayıncının VOD ve kliplerine ek olarak YouTube fan/kesit kanalları arama sorguları üzerinden taranır.
5. **WebUI & API:** Kullanıcının arayüzden yayıncı eklerken fan arama terimlerini ve yeni kurgu modlarını seçebilmesi sağlanır.

**Tech Stack:** Python 3.11, FastAPI, FFmpeg 7.0, faster-whisper, Pillow (PIL), OpenCV / MediaPipe / Inset Geometry, Google YouTube Data API v3, SQLite.

---

### Task 1: Veri Modellerini Genişletme (`clip_schema.py`)
- `ClipPipelineSettings`: `hook_overlay_enabled: bool = True`, `video_layout: str = "streamer_facecam"` seçeneği.
- `ClipItem`: `hook_text: Optional[str]`, `viral_score: Optional[int]`.
- `StreamerTarget`: `fan_channel_queries: List[str] = []`.
- SQLite migration / schema check in `clip_db.py`.

### Task 2: LLM Tabanlı Viral An & Kanca Seçici (`hype_detector.py`)
- OpenShorts'un transkript pencereleme (scoring windows) mantığını `hype_detector.py` içine ekle.
- `llm.py` kullanarak transkriptten `(start_sec, end_sec, hook_text, title, score)` çıkaran `analyze_viral_moments_llm` fonksiyonunu yaz.
- Whisper çıktısı yoksa veya LLM yanıt vermezse mevcut audio peak (desibel analizi) fallback olarak çalışmaya devam etsin.

### Task 3: Kanca (Hook) Görseli Üretici (`clip_editor.py` / `hook_renderer.py`)
- OpenShorts'un `hooks.py` mantığını kullanarak video üzerine bindirilecek yarı şeffaf / renkli zeminli şık kanca görseli (PNG) üreten fonksiyon yaz.
- FFmpeg overlay filtresine bu kanca görselini ekle (videonun ilk 5-8 saniyesinde veya tamamında üstte görünsün).

### Task 4: Akıllı Yayıncı Düzeni (Facecam + Gameplay Inset)
- OpenShorts'un `camera_inset.py` mantığını adapte et:
  - 16:9 karedeki köşe facecam kutusunu tespit et veya köşe tahmini yap.
  - FFmpeg filter_complex: Üst panele facecam kutusu ölçeklenir, alt panele ana oyun ekranı yerleştirilir.
- Test videosu ile render doğrula.

### Task 5: Fan Kanalları ve Yayın Geçmişi Taramasını Güçlendirme (`clip_downloader.py`)
- `StreamerTarget` içindeki `fan_channel_queries` ve YouTube arama mantığını birleştir.
- Belirtilen yayıncı için (örn. Rraenee) hem Twitch/Kick VOD/kliplerini hem de YouTube'daki fan kanallarını (`rraenee kesitleri`, `rraenee klipleri`) otomatik tarayıp stoka atsın.

### Task 6: Uçtan Uca Entegrasyon, Testler ve Doğrulama
- Mevcut 56 testin kırılmadığını doğrula.
- Yeni eklenen facecam layout, hook overlay ve LLM viral an tespiti için kapsamlı birim ve entegrasyon testleri yaz.
- Tüm testleri çalıştır ve doğrula.
