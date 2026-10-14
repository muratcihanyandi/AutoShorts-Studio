# Twitch & Kick Klip ve Shorts Otomasyonu Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** MoneyPrinterTurbo projesini iki sekmeli (tabbed) bir yapıya kavuşturarak, Twitch ve Kick yayıncılarının kliplerini otonom zamanlayıcı (Scheduler) veya harici AI Ajanları (Localhost REST API / MCP) aracılığıyla otomatik indiren, hype analizi ile en can alıcı anı seçen, 9:16 Shorts formatına çevirip kelime vurgulu altyazı ekleyen ve YouTube'a yükleyen uçtan uca bir sistem inşa etmek.

**Architecture:** 
1. **Arayüz Katmanı:** Streamlit `Main.py` refactor edilerek iki sekmeye ayrılır: `Sekme 1: AI Video Üretici (Mevcut Sistem)`, `Sekme 2: Yayıncı Klip & Shorts Otomasyonu`.
2. **Çift Motorlu Kontrol (Dual-Engine):** 
   - *Mod A (Otonom Kural Motoru):* Sistem açık olduğu sürece arka planda çalışan zamanlayıcı (Worker thread), belirlenen periyotlarda Twitch/Kick kanallarını tarar ve iş akışını yürütür.
   - *Mod B (Localhost AI Agent Portu):* FastAPI üzerinde açılan `/api/v1/clips/*` REST endpoint'leri ile Antigravity / Cursor / Claude / GPT ajanları sisteme bağlanarak klipleri bulma, indirme, kurgulama ve YouTube'a yükleme komutlarını uzaktan yönetebilir.
3. **İşleme Hattı (Pipeline):** `yt-dlp` ile indirme -> Audio Peak & LLM ile Hype/Highlight tespiti -> FFmpeg ile 9:16 Bulanık Arka Plan & Split Screen -> `faster-whisper` ile dinamik altyazı -> `YouTubeUploaderService` ile YouTube Shorts yükleme.

**Tech Stack:** Python 3.10+, Streamlit 1.59, FastAPI, yt-dlp, faster-whisper, FFmpeg 7.0, Google YouTube Data API v3, SQLite (klip geçmişi ve durum takibi).

---

## 1. Mimari Genel Bakış (Architecture Blueprint)

```
+--------------------------------------------------------------------------+
|                      STREAMLIT WEBUI (Main.py)                           |
|  +----------------------------------+  +-------------------------------+ |
|  | Tab 1: MoneyPrinter AI Video     |  | Tab 2: Twitch/Kick Clip Engine| |
|  | (Mevcut Video Üretim Ekranı)     |  | (Yayıncı Listesi, Durum, Log) | |
|  +----------------------------------+  +---------------+---------------+ |
+--------------------------------------------------------|-----------------+
                                                         |
                   +-------------------------------------+-----------------------------------+
                   |                                                                         |
        [KULLANICI SEÇENEK 1]                                                     [KULLANICI SEÇENEK 2]
    +------------------------------+                                          +------------------------------+
    | Otonom Kural Motoru          |                                          | Localhost REST API Portu     |
    | (Background Scheduler)       |                                          | (FastAPI / OpenAPI / Agent)  |
    | - Belirlenen periyotta tara  |                                          | - Harici AI Ajanı komut verir|
    | - Yeni popüler klibi yakala  |                                          | - Klip arama, indirme, kesme|
    +--------------+---------------+                                          +--------------+---------------+
                   |                                                                         |
                   +-------------------------------------+-----------------------------------+
                                                         |
                                                         v
                                      +------------------------------------+
                                      |     Clip Pipeline Manager          |
                                      +------------------------------------+
                                                         |
          +----------------------------------------------+----------------------------------------------+
          |                                              |                                              |
          v                                              v                                              v
   [1. FETCH & DOWNLOAD]                        [2. HYPE & HIGHLIGHT]                        [3. 9:16 CROP & SUBTITLE]
   - Twitch Helix API / GQL                     - Audio Peak Detection (dB/RMS)              - FFmpeg 9:16 Blur Background
   - Kick Public API                            - Whisper Transcript + LLM                   - faster-whisper Kelime Vurgulu
   - yt-dlp High-Res MP4                        - En vurucu 30-59 saniye                     - ASS/SRT Burn-in
          |                                              |                                              |
          +----------------------------------------------+----------------------------------------------+
                                                         |
                                                         v
                                            [4. YOUTUBE SHORTS UPLOAD]
                                            - YouTube Data API v3
                                            - LLM ile Viral Başlık/Tag Üretimi
                                            - SQLite DB: İşlenen Klipler Logu
```

---

## Task 1: WebUI Sekmeli Yapıya Geçiş (Tabbed Layout)

**Amaç:** `MoneyPrinterTurbo/webui/Main.py` dosyasındaki mevcut yapıyı bozmadan, kullanıcı deneyimini iki ana sekmeye ayırmak.

**Dosyalar:**
- Oluştur: `MoneyPrinterTurbo/webui/views/moneyprinter_view.py` (Mevcut video üretim UI mantığı)
- Oluştur: `MoneyPrinterTurbo/webui/views/clips_automation_view.py` (Yeni Klip Otomasyon UI arayüzü)
- Düzenle: `MoneyPrinterTurbo/webui/Main.py`

**Adımlar:**
1. `Main.py` içerisindeki mevcut video üretim bileşenlerini `moneyprinter_view.py` içine modüler olarak taşı veya `tab1, tab2 = st.tabs(["🎬 AI Video Oluşturucu", "⚡ Twitch & Kick Klip Otomasyonu"])` yapısı kur.
2. `tab1` içinde mevcut `render_moneyprinter_ui()` çağrısı yer alsın.
3. `tab2` içinde yeni klip otomasyon dashboard'u yüklensin.
4. Streamlit oturum durumu (`st.session_state`) izole edilerek iki sekmenin birbirinin ayarlarını ezmesi engellensin.

---

## Task 2: Klip Veri Modeli ve Veritabanı (SQLite / Tracking DB)

**Amaç:** Daha önce indirilip işlenen kliplerin tekrar işlenmesini önlemek, hangi klip ne zaman işlendi, YouTube'a yüklendi mi, linki nedir gibi bilgileri saklamak.

**Dosyalar:**
- Oluştur: `MoneyPrinterTurbo/app/models/clip_schema.py`
- Oluştur: `MoneyPrinterTurbo/app/services/clip_db.py`
- Test: `MoneyPrinterTurbo/test/services/test_clip_db.py`

**Adımlar:**
1. `ClipInfo` ve `StreamerTarget` Pydantic modellerini tanımla (platform: Twitch/Kick, broadcaster_name, clip_id, title, duration, view_count, status, youtube_video_id, created_at).
2. SQLite veritabanı bağlantısı kur (`storage/clips.db`).
3. Fonksiyonlar: `save_clip()`, `is_clip_processed(clip_id)`, `get_streamers()`, `add_streamer()`, `update_clip_status()`, `list_recent_clips()`.
4. Test yaz ve doğrula.

---

## Task 3: Twitch & Kick Klip Arama ve İndirme Servisi (`ClipDownloaderService`)

**Amaç:** Belirtilen yayıncıların popüler ve yeni kliplerini çekmek, `yt-dlp` kullanarak en yüksek kalitede indirmek.

**Dosyalar:**
- Oluştur: `MoneyPrinterTurbo/app/services/clip_downloader.py`
- Test: `MoneyPrinterTurbo/test/services/test_clip_downloader.py`

**Adımlar:**
1. **Twitch Klip Yakalama:**
   - Twitch Helix API veya `yt-dlp` ile yayıncının son 24 saat veya son 7 gün içindeki en çok izlenen kliplerini sorgula.
   - Kliplerin ID, URL, Başlık, Süre, İzlenme sayısı ve Önizleme resmini topla.
2. **Kick Klip Yakalama:**
   - Kick API (`https://kick.com/api/v2/channels/{slug}/clips`) ve `yt-dlp` desteğiyle yayıncının en son ve en çok izlenen kliplerini sorgula.
3. **İndirme Motoru:**
   - `yt-dlp` komutunu Python `subprocess` veya `yt_dlp.YoutubeDL` kütüphanesi üzerinden çağırarak videoyu `storage/clips/raw/{platform}_{clip_id}.mp4` yoluna kaydet.
4. Daha önce işlenmiş klipleri `clip_db` üzerinden filtreleyerek atla.

---

## Task 4: Hype & Highlight Analiz Servisi (`HypeDetectorService`)

**Amaç:** İndirilen videonun (klip veya VOD kesiti) en heyecanlı, en yüksek enerjili 30-59 saniyelik kısmını belirlemek.

**Dosyalar:**
- Oluştur: `MoneyPrinterTurbo/app/services/hype_detector.py`
- Test: `MoneyPrinterTurbo/test/services/test_hype_detector.py`

**Adımlar:**
1. **Ses Şiddeti (Audio Peak) Analizi:**
   - FFmpeg `astats` / `ebur128` filtresi veya Python ses analizi ile saniyeler bazında ses RMS / desibel seviyelerini çıkar.
   - Yayıncının bağırdığı, güldüğü veya aksiyonun tavan yaptığı tepe noktaları (peaks) tespit et.
2. **Whisper + LLM Semantik Analiz (Opsiyonel & Gelişmiş):**
   - Klibin sesini Whisper ile transkribe et.
   - MoneyPrinterTurbo'nun mevcut `llm.py` servisine metni gönder: *"Bu metindeki en heyecan verici/komik 30-45 saniyelik aralığı (start_sec, end_sec) ve viral olabilecek 1 başlık belirle."*
3. **Zaman Damgası Seçimi:**
   - Klip zaten <60 saniye ise: Klibin tamamı veya en iyi 30-45 saniyesi seçilir.
   - Klip >60 saniye ise: Hype skoru en yüksek olan 30-50 saniyelik dilim kesim için işaretlenir.

---

## Task 5: 9:16 Shorts Video Kurgu & Dinamik Altyazı Servisi (`ClipEditorService`)

**Amaç:** 16:9 yatay klibi modern 9:16 dikey YouTube Shorts formatına çevirmek ve dinamik kelime vurgulu altyazı basmak.

**Dosyalar:**
- Oluştur: `MoneyPrinterTurbo/app/services/clip_editor.py`
- Test: `MoneyPrinterTurbo/test/services/test_clip_editor.py`

**Adımlar:**
1. **FFmpeg 9:16 Formatlandırma Şablonları:**
   - **Şablon 1 (Blur Background - Önerilen):** Orijinal 16:9 video dikey ölçeklenir ve bulanıklaştırılır (arka plan), keskin orijinal video ortada gösterilir.
   - **Şablon 2 (Center Crop):** Aksiyon ortalanarak 1080x1920 kırpılır.
   - **Şablon 3 (Split Screen):** Kamera yukarıda, oyun görüntüsü aşağıda (yayıncı klip şablonu).
2. **Kelime Düzeyinde Altyazı Basma (Word-level Captions):**
   - Projedeki `faster-whisper` ile kelime zaman damgaları (word timestamps) çıkar.
   - TikTok/Shorts tarzı renkli, vurgulu (highlighted word) `.ass` altyazı dosyası üret.
   - FFmpeg ile altyazıyı videoya göm (hardcode burn-in).
3. Çıktı: `storage/clips/ready/{clip_id}_shorts.mp4`.

---

## Task 6: YouTube Shorts Yükleme & Metadata Oluşturucu (`ShortsPublisherService`)

**Amaç:** Hazırlanan 9:16 videoyu YouTube'a Shorts olarak yüklemek.

**Dosyalar:**
- Oluştur: `MoneyPrinterTurbo/app/services/shorts_publisher.py`
- Düzenle: `MoneyPrinterTurbo/app/services/youtube_uploader.py` (Gerekirse Shorts tag/privacy eklemeleri)

**Adımlar:**
1. Mevcut `YouTubeUploaderService` sınıfını kullanarak OAuth2 doğrulamasını kontrol et.
2. Başlık sonuna `#shorts #twitch #kick` etiketlerini ekle.
3. LLM kullanarak yayıncı adı + klip içeriğine göre yüksek tıklanma oranlı (clickbait/viral) başlık ve açıklama üret.
4. YouTube API ile videoyu yükle, video ID ve URL'sini `clip_db`'ye kaydet.

---

## Task 7: Çift Mod 1 - Otonom Kural Motoru (Background Scheduler Worker)

**Amaç:** Sistem açık kaldığı sürece belirlenen aralıklarla döngü halinde çalışarak yayıncıları tarayıp otomasyonu yürütmek.

**Dosyalar:**
- Oluştur: `MoneyPrinterTurbo/app/services/clip_scheduler.py`

**Adımlar:**
1. Arka plan thread/worker oluştur (`threading.Thread` veya `asyncio`).
2. Kullanıcının UI'dan belirlediği parametreleri oku:
   - Kontrol Aralığı (Örn: 30 dk, 1 saat, 3 saat)
   - Yayıncı Listesi (Twitch: [xQc, Elraenn], Kick: [x, y])
   - Minimum İzlenme Eşiği (Örn: 500 views)
   - Otomatik Yükleme Açık/Kapalı (Auto-Upload vs Onay Bekleme)
3. Her periyotta:
   - Yeni klip var mı kontrol et -> İndir -> Hype kes -> 9:16 yap & Altyazı ekle -> YouTube'a yükle -> Log yaz.
4. UI'a anlık log ve durum akışı sağla.

---

## Task 8: Çift Mod 2 - Localhost REST API Portu (AI Agent Entegrasyonu)

**Amaç:** FastAPI üzerinden yapay zeka ajanlarının (Antigravity, Cursor, harici scriptler) tam kontrol sağlayabileceği endpoint'ler açmak.

**Dosyalar:**
- Oluştur: `MoneyPrinterTurbo/app/controllers/v1/clips.py`
- Düzenle: `MoneyPrinterTurbo/app/router.py` (Yeni clip router'ını bağla)

**Adımlar:**
1. REST API Endpoint'leri:
   - `GET /api/v1/clips/streamers`: Takip edilen yayıncıları listele.
   - `POST /api/v1/clips/streamers`: Yeni yayıncı ekle/sil.
   - `POST /api/v1/clips/discover`: Belirli platform ve yayıncı için yeni klipleri getir.
   - `POST /api/v1/clips/download`: Belirtilen klip URL'sini indir.
   - `POST /api/v1/clips/process`: İndirilen klibi 9:16 yap, altyazı bas.
   - `POST /api/v1/clips/publish`: YouTube'a yükle.
   - `POST /api/v1/clips/pipeline/run-single`: URL veya yayıncı adı verip uçtan uca tek adımda tamamla.
2. OpenAPI Swagger dökümanı (`http://127.0.0.1:8080/docs`) üzerinden AI ajanlarının ve LLM tool calling mekanizmalarının doğrudan tüketebileceği JSON şemaları sağla.

---

## Task 9: Streamlit WebUI Sekme 2 Tasarımı (Streamer Dashboard)

**Amaç:** Sekme 2 için modern, sezgisel, Türkçe ve işlevsel bir kontrol paneli hazırlamak.

**Dosyalar:**
- Oluştur: `MoneyPrinterTurbo/webui/views/clips_automation_view.py`

**Arayüz Bileşenleri:**
1. **Üst Bilgi & Çalışma Modu Seçici:**
   - 🔘 "Otonom Zamanlayıcı (Arka Planda Otomatik Çalış)"
   - 🔘 "AI Agent / API Modu (Localhost Portu Aktif)"
2. **Yayıncı Yönetim Kartı:**
   - Yayıncı Ekle (Platform: Twitch / Kick, Kullanıcı Adı)
   - Aktif Yayıncılar Listesi (Silme/Düzenleme butonları)
3. **Klip Filtreleme & Kurgu Ayarları:**
   - Min İzlenme Sayısı, Klip Süresi (Maks 60s)
   - Video Düzeni (Blur Background, Center Crop, Split)
   - Altyazı Yazı Tipi, Rengi ve Vurgu Stili
4. **YouTube Entegrasyon Kartı:**
   - YouTube Bağlantı Durumu (Bağlı / Token Bekleniyor)
   - Varsayılan Gizlilik (Gizli / Liste Dışı / Herkese Açık)
5. **Canlı Akış & İşlem Geçmişi (Tablo + Video Önizleme):**
   - Son indirilen ve kurgulanan videoların oynatıcısı
   - YouTube yükleme durumu ve linkler.

---

## Task 10: Entegrasyon, Test ve Canlıya Alma

**Adımlar:**
1. Örnek bir Twitch/Kick klibi ile uçtan uca test:
   - Klip çekme -> İndirme -> Hype tespiti -> 9:16 dönüştürme -> Whisper altyazı -> YouTube taslak yükleme.
2. WebUI'da her iki sekmenin sorunsuz açıldığının doğrulanması.
3. REST API Swagger testleri.
4. Başlatma scriptlerinin (`start.bat` ve `api.bat`) yeni mimariyi sorunsuz çalıştırdığının teyidi.

---

## 2026-10-14 Guncelleme: Stok + Gunluk Kota + Yatay Hat + Coklu Kaynak

Kullanicinin yeni gereksinimleri dogrultusunda plan genisletildi ve uygulandi:

1. **Stok modeli:** Kesif artik bulunan tum icerikleri `DISCOVERED` olarak stoga yazar
   (once sadece ilk klip islenip digerleri kayboluyordu). Islem hatti stoktan beslenir,
   `stock_max_ready_per_format` ile sinirlidir.
2. **Gunluk kota:** `clip_publisher.py` eklendi. `daily_shorts_limit` / `daily_horizontal_limit`
   (varsayilan 3+3), `publish_interval_minutes` ve `publish_window_start/end_hour` ile
   stoktan gunluk sinirli otomatik yukleme yapilir; fazlasi stokta bekler.
3. **Yatay (16:9) hat:** `ClipEditorService.process_clip_horizontal` eklendi.
   Intro/outro `silencedetect + blackdetect` ile kirpilir, govde korunur, **altyazi eklenmez**.
   Uzun VOD'lar `horizontal_max_part_minutes` (varsayilan 15 dk) parcalara bolunur.
4. **Coklu kaynak:** Twitch/Kick VOD (yayin gecmisi), YouTube kanal videolari ve
   YouTube arama (`ytsearch`, `youtube_search_enabled`) kaynaklari eklendi.
   Canli yayinlar VOD uzerinden islenir (kullanici tercihi).
5. **Platform eklemeleri:** `PlatformType.YOUTUBE`; URL cozumleme
   (`parse_streamer_url`: twitch.tv/x, kick.com/x, youtube.com/@x) WebUI ve API'de aktif.
   Her yayinci icin format secimi: `formats=["shorts","horizontal"]`.
6. **Tek indirme:** Ayni kaynagin shorts/yatay ikizleri `source_key` uzerinden tek dosyayi paylasir.
7. **Altyazi iyilestirmesi:** Kelime bazli vurgulama (aktif kelime renkli) gercekten uygulandi;
   `subtitle_color` ayari artik kullanilir.
8. **LLM metadata:** Kirik `analyze_hype_semantic_llm` kaldirildi; `llm.generate_social_metadata`
   (tr) ile viral baslik/aciklama/etiket uretimi `shorts_publisher` icine baglandi (fallback sablonlu).
9. **Ek endpoint'ler:** `GET /stock`, `GET /quota`, `POST /clips/{id}/priority`,
   `POST /clips/{id}/process` (async), `DELETE /clips/{id}`, `POST /detect-trim`;
   `custom_start_sec/custom_end_sec` artik gercekten uygulanir.
10. **Testler:** `test_clip_publisher`, `test_clip_downloader`, `test_clip_horizontal_render`
    eklendi; `test_clip_db` goc + stok sorgu testleriyle genisletildi (toplam 36 test).
