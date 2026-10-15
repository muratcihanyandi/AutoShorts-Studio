from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class PlatformType(str, Enum):
    TWITCH = "twitch"
    KICK = "kick"
    YOUTUBE = "youtube"


class ClipTargetFormat(str, Enum):
    """Klibin hedef cikti formati: dikey Shorts veya yatay normal video."""
    SHORTS = "shorts"
    HORIZONTAL = "horizontal"


class ClipSourceKind(str, Enum):
    """Klibin kaynagi: yayinci klibi, VOD (yayin gecmisi), YouTube videosu veya VOD parcasi."""
    CLIP = "clip"
    VOD = "vod"
    YOUTUBE_VIDEO = "youtube_video"
    VOD_PART = "vod_part"


class ClipStatus(str, Enum):
    DISCOVERED = "discovered"
    DOWNLOADING = "downloading"
    DOWNLOADED = "downloaded"
    ANALYZING = "analyzing"
    EDITING = "editing"
    READY = "ready"
    UPLOADING = "uploading"
    PUBLISHED = "published"
    SPLIT = "split"  # VOD parcalara ayrildi; yayinlanabilir birimler parcalar
    FAILED = "failed"
    SKIPPED = "skipped"


DEFAULT_STREAMER_FORMATS = [ClipTargetFormat.SHORTS, ClipTargetFormat.HORIZONTAL]


class StreamerTarget(BaseModel):
    id: Optional[int] = None
    platform: PlatformType
    channel_name: str
    display_name: Optional[str] = None
    is_active: bool = True
    formats: List[ClipTargetFormat] = Field(default_factory=lambda: list(DEFAULT_STREAMER_FORMATS))
    min_views: int = 100
    max_duration_seconds: int = 60
    check_interval_minutes: int = 60
    fan_channel_queries: List[str] = Field(default_factory=list)
    last_checked_at: Optional[datetime] = None
    created_at: Optional[datetime] = Field(default_factory=datetime.utcnow)


class ClipItem(BaseModel):
    id: Optional[int] = None
    clip_id: str
    platform: PlatformType
    channel_name: str
    title: str
    url: str
    duration: float = 0.0
    view_count: int = 0
    thumbnail_url: Optional[str] = None
    created_at_platform: Optional[str] = None

    # Hedef cikti ve kaynak tipi
    target_format: ClipTargetFormat = ClipTargetFormat.SHORTS
    source_kind: ClipSourceKind = ClipSourceKind.CLIP
    # Indirme dedupe anahtari (ayni kaynaktan shorts + yatay cikarken tek indirme)
    source_key: Optional[str] = None

    # Islem durumlari
    status: ClipStatus = ClipStatus.DISCOVERED
    raw_video_path: Optional[str] = None
    processed_video_path: Optional[str] = None
    subtitle_path: Optional[str] = None
    transcript: Optional[str] = None

    # Hype ve Viral Moment Analizi (OpenShorts)
    hype_score: float = 0.0
    viral_score: int = 0
    hook_text: Optional[str] = None
    cut_start_sec: float = 0.0
    cut_end_sec: float = 0.0

    # Yayin onceligi (yuksek olan once yuklenir)
    priority: int = 0

    # YouTube verileri & Filtreleri
    youtube_title: Optional[str] = None
    youtube_description: Optional[str] = None
    youtube_tags: Optional[List[str]] = None
    youtube_video_id: Optional[str] = None
    youtube_status: Optional[str] = None
    youtube_category_id: Optional[str] = None
    made_for_kids: Optional[bool] = False
    age_restricted: Optional[bool] = False
    has_paid_promotion: Optional[bool] = False
    contains_synthetic_media: Optional[bool] = False
    custom_thumbnail_path: Optional[str] = None
    scheduled_publish_time: Optional[datetime] = None
    published_at: Optional[datetime] = None

    error_message: Optional[str] = None
    is_vertical: Optional[bool] = None
    has_subtitles: Optional[bool] = None
    created_at: Optional[datetime] = Field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = Field(default_factory=datetime.utcnow)


class ClipPipelineSettings(BaseModel):
    # Otomatik yayinlama (gunluk kota ile sinirli stoktan yukleme)
    auto_upload: bool = False
    privacy_status: str = "private"  # private, unlisted, public

    # Gunluk yukleme kontenjanlari
    daily_shorts_limit: int = Field(default=3, ge=1)
    daily_horizontal_limit: int = Field(default=3, ge=1)
    publish_interval_minutes: int = Field(default=30, ge=1)
    publish_window_start_hour: int = 9   # yerel saat
    publish_window_end_hour: int = 24    # yerel saat

    # Prime Time & Akilli Yayinlama
    prime_time_slots: str = "18:00, 20:00, 22:00"  # En yuksek etkilesim saatleri (Slot 1, Slot 2, Slot 3)
    enforce_prime_time: bool = True                # Yalnizca Prime Time slotlarinda mi yayinlansin?

    # Hedef Stok Yonetimi (Yatay: 3, Shorts: 6)
    target_stock_horizontal: int = 3
    target_stock_shorts: int = 6
    stock_max_ready_per_format: int = 15
    keep_raw_files: bool = False

    # YouTube Filtre & Kategori Ayarlari
    youtube_category_id: str = "20"         # 20: Gaming, 22: People & Blogs, 24: Entertainment
    made_for_kids: bool = False             # Cocuklara ozel icerik mi?
    age_restricted: bool = False            # 18+ yas siniri
    has_paid_promotion: bool = False        # Ucretli tanitim / urun yerlestirme var mi? (Varsayilan Hayir)
    contains_synthetic_media: bool = False  # Degistirilmis veya yapay zeka icerik var mi? (Varsayilan Hayir)
    auto_generate_thumbnail: bool = True    # Peak hype karelerinden otomatik kapak gorseli uret

    shorts_source_mode: str = "smart_hybrid"  # smart_hybrid (dikey bulursa korur, yataysa facecam+oyun kurgular), only_native_shorts (sadece hazir dikey), convert_horizontal (sadece yataydan kurgula)
    video_layout: str = "streamer_facecam"    # streamer_facecam (webcam ustte, oyun altta), center_crop, split
    facecam_position: str = "auto"            # auto (otomatik algila), bottom_left, bottom_right, top_left, top_right
    skip_subtitles_if_detected: bool = True   # Videoda onceden altyazi varsa cift altyazi yapmamak icin whisper'i atla
    hook_overlay_enabled: bool = True
    hook_position: str = "top"                # top, middle
    llm_moment_detection: bool = True
    subtitle_enabled: bool = True
    subtitle_font: str = "MicrosoftYaHeiBold.ttc"
    subtitle_font_size: int = 24
    subtitle_color: str = "#FFD700"  # Gold
    shorts_window_sec: float = 40.0
    min_view_threshold: int = 100
    max_clip_length: int = 60

    # Yatay (16:9) hatti
    horizontal_max_part_minutes: float = 15
    vod_max_minutes: int = 180
    filter_horizontal_reactions_only: bool = True  # Yatay videolarda duz funny moments/derlemeleri ele, sadece yayincinin izledigi/tepki verdigi anlari kabul et

    # YouTube kaynak aramasi & Guncellik / Viral Filtresi
    youtube_search_enabled: bool = True
    youtube_source_max_minutes: int = 30
    youtube_query_suffix: str = "kesitleri klipleri"
    enable_viral_hunter: bool = False           # Eski viral videolari avlama (Kapali: yalnizca son max_video_age_days gunun videolari secilir)
    max_video_age_days: int = 7                 # Son 7 gun (1 hafta) icindeki guncel videolar
    viral_threshold_for_older: int = 50000      # Viral avi acikken bu izlenmenin ustundeki eski videolari kabul et
    title_blacklist: str = "açıklama, sorun, duyuru, bilgilendirme, özür, yayın saat, kickte sorun, kick'te sorun"
    enable_kick_discovery: bool = False         # Kick klipleri varsayilan kapali (bos/sacma klipleri engellemek icin)
    enable_twitch_discovery: bool = True

    # Kurgu: Intro / Outro otomatik kirpma (jenerik ve bitis kartlarini keser)
    intro_trim_seconds: float = 5.0             # Videonun basindan kirpilacak intro suresi
    outro_trim_seconds: float = 6.0             # Videonun sonundan kirpilacak outro suresi

    # Bant genisligi & Indirme Hiz Limiti (Internetin yavaslamamasi icin)
    download_rate_limit_mb: float = 0.0         # 0.0 = Limitsiz (Maksimum hiz), ornegin 5.0 -> 5 MB/s ile sinirla

    # AI Baslik, Aciklama ve SEO Promptu (Kullanici tarafindan ozellestirilebilir)
    ai_seo_system_prompt: str = ""

    # Taslak Olarak Onceden Yukleme (Zamani geldiginde aninda herkese acik yapilir)
    pre_upload_as_draft: bool = True

    ai_title_generation: bool = True
    check_interval_minutes: int = 60
