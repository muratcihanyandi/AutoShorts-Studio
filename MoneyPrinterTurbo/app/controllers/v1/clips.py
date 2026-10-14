from typing import List, Optional
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field
from loguru import logger

from app.models.clip_schema import (
    PlatformType,
    ClipItem,
    ClipStatus,
    StreamerTarget,
    ClipPipelineSettings,
    ClipTargetFormat,
)
from app.services.clip_db import clip_db
from app.services.clip_downloader import clip_downloader
from app.services.hype_detector import hype_detector
from app.services.clip_editor import clip_editor
from app.services.shorts_publisher import shorts_publisher
from app.services.clip_publisher import clip_publisher
from app.services.clip_scheduler import clip_scheduler

router = APIRouter(prefix="/api/v1/clips", tags=["Clips Automation & AI Agent API"])


# ================= Request Modelleri =================

class StreamerAddRequest(BaseModel):
    platform: Optional[PlatformType] = None
    channel_name: Optional[str] = None
    channel_url: Optional[str] = Field(
        default=None,
        description="Tam yayinci adresi (twitch.tv/x, kick.com/x, youtube.com/@x). Verilirse platform ve kanal adi otomatik cozulur."
    )
    display_name: Optional[str] = None
    formats: List[str] = Field(default_factory=lambda: ["shorts", "horizontal"],
                               description="Uretilecek cikti formatlari: shorts, horizontal")
    min_views: int = 100
    max_duration_seconds: int = 60
    check_interval_minutes: int = 60
    fan_channel_queries: List[str] = Field(default_factory=list, description="YouTube fan veya kesit kanali arama sorgulari")


class DiscoverClipsRequest(BaseModel):
    platform: PlatformType
    channel_name: str
    source: str = Field(default="clips", description="clips | vods | youtube")
    limit: int = 10
    min_views: int = 0


class DirectClipPipelineRequest(BaseModel):
    url: str
    platform: Optional[PlatformType] = None
    channel_name: Optional[str] = "unknown"
    title: Optional[str] = None
    target_format: ClipTargetFormat = ClipTargetFormat.SHORTS
    auto_upload_youtube: bool = False
    custom_start_sec: Optional[float] = None
    custom_end_sec: Optional[float] = None


class RenderShortsRequest(BaseModel):
    clip_id: str
    video_layout: str = "blur_bg"  # blur_bg, center_crop, split
    subtitle_enabled: bool = True
    start_sec: float = 0.0
    end_sec: float = 0.0


class UploadYouTubeRequest(BaseModel):
    clip_id: str
    title: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[List[str]] = None
    privacy_status: str = "private"


class PriorityUpdateRequest(BaseModel):
    priority: int = Field(description="Yuksek deger = once yuklenir")


class ProcessClipRequest(BaseModel):
    custom_start_sec: Optional[float] = None
    custom_end_sec: Optional[float] = None
    publish_now: bool = False


# ================= Yardimcilar =================

def _parse_formats(raw_formats: List[str]) -> List[ClipTargetFormat]:
    try:
        return [ClipTargetFormat(f) for f in raw_formats]
    except ValueError:
        raise HTTPException(status_code=400, detail="Gecersiz format. Gecerli degerler: shorts, horizontal")


def _process_clip_job(clip_id: str,
                      custom_start_sec: Optional[float],
                      custom_end_sec: Optional[float],
                      publish_now: bool):
    """Arka planda tek klip isleme (BackgroundTasks icin)."""
    clip = clip_db.get_clip_by_id(clip_id)
    if not clip:
        logger.error(f"Process job: clip not found {clip_id}")
        return
    try:
        clip_scheduler.run_single_pipeline_for_clip(
            clip,
            custom_start_sec=custom_start_sec,
            custom_end_sec=custom_end_sec,
            publish_now=publish_now
        )
    except Exception as e:
        logger.error(f"Process job failed for {clip_id}: {e}")


# ================= Yayinci Yonetimi =================

@router.get("/streamers", response_model=List[StreamerTarget], summary="Takip edilen yayıncıları listele")
def get_streamers(active_only: bool = False):
    return clip_db.list_streamers(active_only=active_only)


@router.post("/streamers", summary="Yeni yayıncı ekle veya güncelle (URL veya platform+kanal adı)")
def add_streamer(req: StreamerAddRequest):
    formats = _parse_formats(req.formats)
    if not formats:
        raise HTTPException(status_code=400, detail="En az bir cikti formati secilmeli.")

    platform = req.platform
    channel_name = (req.channel_name or "").strip()

    # URL verilmisse platform ve kanal adini adresten coz
    if req.channel_url:
        try:
            platform, channel_name = clip_downloader.parse_streamer_url(req.channel_url)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    if not platform or not channel_name:
        raise HTTPException(status_code=400, detail="platform+channel_name veya channel_url gerekli.")

    target = StreamerTarget(
        platform=platform,
        channel_name=channel_name,
        display_name=req.display_name or channel_name,
        formats=formats,
        min_views=req.min_views,
        max_duration_seconds=req.max_duration_seconds,
        check_interval_minutes=req.check_interval_minutes,
        fan_channel_queries=req.fan_channel_queries,
    )
    s_id = clip_db.add_streamer(target)
    return {
        "status": "success",
        "streamer_id": s_id,
        "platform": platform.value,
        "channel_name": channel_name,
        "formats": [f.value for f in formats],
        "message": f"{channel_name} eklendi."
    }


@router.delete("/streamers/{streamer_id}", summary="Yayıncıyı takipten çıkar")
def delete_streamer(streamer_id: int):
    success = clip_db.delete_streamer(streamer_id)
    if not success:
        raise HTTPException(status_code=404, detail="Yayıncı bulunamadı.")
    return {"status": "success", "message": "Yayıncı silindi."}


# ================= Kesif =================

@router.post("/discover", response_model=dict, summary="Yayıncının içeriklerini tara (klip / VOD / YouTube) ve stoğa ekle")
def discover_clips(req: DiscoverClipsRequest):
    source = (req.source or "clips").lower()
    items: List[ClipItem] = []

    try:
        if source == "clips":
            if req.platform == PlatformType.TWITCH:
                items = clip_downloader.fetch_twitch_clips(req.channel_name, limit=req.limit, min_views=req.min_views)
            elif req.platform == PlatformType.KICK:
                items = clip_downloader.fetch_kick_clips(req.channel_name, limit=req.limit, min_views=req.min_views)
            else:
                items = clip_downloader.fetch_youtube_channel_videos(req.channel_name, limit=req.limit, min_views=req.min_views)
        elif source == "vods":
            if req.platform == PlatformType.KICK:
                items = clip_downloader.fetch_kick_vods(req.channel_name, limit=req.limit)
            else:
                items = clip_downloader.fetch_twitch_vods(req.channel_name, limit=req.limit)
        elif source == "youtube":
            items = clip_downloader.fetch_youtube_videos(req.channel_name, limit=req.limit, min_views=req.min_views)
        else:
            raise HTTPException(status_code=400, detail="Gecersiz kaynak. Gecerli degerler: clips, vods, youtube")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Tarama hatasi: {str(e)}")

    # Bulunanlari stoga yaz (dedupe)
    added = 0
    for item in items:
        if not clip_db.is_clip_processed(item.clip_id):
            clip_db.save_clip(item)
            added += 1

    return {
        "status": "success",
        "source": source,
        "found": len(items),
        "added_to_stock": added,
        "items": [i.model_dump() for i in items]
    }


# ================= Stok =================

@router.get("/stock", summary="Stoktaki videoları listele (format/durum filtreli)")
def get_stock(
    target_format: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0
):
    clips = clip_db.list_clips(limit=limit, offset=offset, status=status, target_format=target_format)
    return {
        "status": "success",
        "count": len(clips),
        "items": clips
    }


@router.delete("/clips/{clip_id}", summary="Stoktan klip kaydını sil")
def delete_clip(clip_id: str):
    if not clip_db.delete_clip(clip_id):
        raise HTTPException(status_code=404, detail="Klip bulunamadı.")
    return {"status": "success", "message": "Klip kaydı silindi."}


@router.post("/clips/{clip_id}/priority", summary="Klip yayın önceliğini ayarla")
def set_clip_priority(clip_id: str, req: PriorityUpdateRequest):
    clip = clip_db.get_clip_by_id(clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Klip bulunamadı.")
    clip_db.update_clip_status(clip_id, clip.status, priority=req.priority)
    return {"status": "success", "clip_id": clip_id, "priority": req.priority}


@router.post("/clips/{clip_id}/process", summary="Bekleyen klibi arka planda işle (indir + kurgula)")
def process_clip(clip_id: str, req: ProcessClipRequest, background_tasks: BackgroundTasks):
    clip = clip_db.get_clip_by_id(clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Klip bulunamadı.")
    if clip.status in (ClipStatus.READY, ClipStatus.PUBLISHED, ClipStatus.SPLIT, ClipStatus.EDITING, ClipStatus.DOWNLOADING):
        raise HTTPException(status_code=400, detail=f"Klip zaten islenmis veya islemde: {clip.status.value}")

    background_tasks.add_task(_process_clip_job, clip_id, req.custom_start_sec, req.custom_end_sec, req.publish_now)
    return {"status": "accepted", "clip_id": clip_id, "message": "Islem arka planda baslatildi."}


# ================= Tekil Islemler =================

@router.post("/download", summary="Belirli bir klibi indir")
def download_clip(clip_id: str):
    clip = clip_db.get_clip_by_id(clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Klip veritabanında bulunamadı.")
    try:
        raw_path = clip_downloader.download_clip(clip)
        return {"status": "success", "raw_path": raw_path, "clip_id": clip_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/detect-hype", summary="Klibin ses tepe noktalarını ve hype süresini bul")
def detect_hype(clip_id: str, window_sec: float = 40.0):
    clip = clip_db.get_clip_by_id(clip_id)
    if not clip or not clip.raw_video_path:
        raise HTTPException(status_code=400, detail="Klip henüz indirilmemiş.")

    start_sec, end_sec, score = hype_detector.analyze_audio_volume_peaks(clip.raw_video_path, target_window_sec=window_sec)
    clip_db.update_clip_status(clip_id, clip.status, cut_start_sec=start_sec, cut_end_sec=end_sec, hype_score=score)
    return {
        "status": "success",
        "clip_id": clip_id,
        "start_sec": start_sec,
        "end_sec": end_sec,
        "hype_score": score
    }


@router.post("/detect-trim", summary="Yatay video için intro/outro (baş/son) sınırlarını bul")
def detect_trim(clip_id: str):
    clip = clip_db.get_clip_by_id(clip_id)
    if not clip or not clip.raw_video_path:
        raise HTTPException(status_code=400, detail="Klip henüz indirilmemiş.")

    start_sec, end_sec = hype_detector.detect_intro_outro_bounds(clip.raw_video_path)
    clip_db.update_clip_status(clip_id, clip.status, cut_start_sec=start_sec, cut_end_sec=end_sec)
    return {"status": "success", "clip_id": clip_id, "start_sec": start_sec, "end_sec": end_sec}


@router.post("/render-shorts", summary="Klibi 9:16 Shorts formatına çevir ve altyazı bas")
def render_shorts(req: RenderShortsRequest):
    clip = clip_db.get_clip_by_id(req.clip_id)
    if not clip or not clip.raw_video_path:
        raise HTTPException(status_code=400, detail="Klip raw dosyası bulunamadı.")

    settings = clip_db.get_settings()
    settings.video_layout = req.video_layout
    settings.subtitle_enabled = req.subtitle_enabled

    try:
        out_path = clip_editor.process_clip_to_shorts(
            clip,
            settings=settings,
            cut_start_sec=req.start_sec,
            cut_end_sec=req.end_sec
        )
        return {"status": "success", "processed_path": out_path, "clip_id": req.clip_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ================= Yayinlama =================

@router.post("/publish-youtube", summary="Hazır videoyu hedef formatında YouTube'a yükle")
def publish_to_youtube(req: UploadYouTubeRequest):
    clip = clip_db.get_clip_by_id(req.clip_id)
    if not clip or not clip.processed_video_path:
        raise HTTPException(status_code=400, detail="İşlenmiş video bulunamadı.")

    if req.title:
        clip.youtube_title = req.title
    if req.description:
        clip.youtube_description = req.description
    if req.tags:
        clip.youtube_tags = req.tags

    settings = clip_db.get_settings()
    settings.privacy_status = req.privacy_status

    try:
        video_id = shorts_publisher.publish_clip(clip, settings=settings)
        return {"status": "success", "youtube_video_id": video_id, "url": f"https://youtu.be/{video_id}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/quota", summary="Günlük yükleme kotası ve stok durumu")
def get_quota():
    settings = clip_db.get_settings()
    return {
        "status": "success",
        "auto_upload": settings.auto_upload,
        "within_publish_window": clip_publisher.is_within_publish_window(settings),
        "quota": clip_publisher.get_quota_summary(settings)
    }


# ================= AI Agent Tek Adım =================

@router.post("/pipeline/run-single", summary="AI Ajanı için Tek Adımda Uçtan Uca Klip İşleme")
def run_single_pipeline(req: DirectClipPipelineRequest):
    """
    AI Ajanının tek bir komutla URL verip arkasına yaslanmasını sağlar.
    URL'den video indirilir, kesim noktaları bulunur, hedef formatta kurgulanır
    (shorts: 9:16 + altyazı / horizontal: 16:9 altyazısız) ve isteğe göre YouTube'a atılır.
    """
    # Platform verilmediyse URL'den cozmeyi dene
    platform = req.platform
    channel_name = req.channel_name or "streamer"
    url = req.url.strip()

    if not platform:
        try:
            platform, parsed_channel = clip_downloader.parse_streamer_url(url)
            channel_name = parsed_channel
        except ValueError:
            platform = PlatformType.TWITCH if "twitch" in url else (
                PlatformType.KICK if "kick" in url else PlatformType.YOUTUBE
            )

    clip_id_slug = f"agent_{abs(hash(url)) % 1000000}"
    clip = ClipItem(
        clip_id=clip_id_slug,
        source_key=clip_id_slug,
        platform=platform,
        channel_name=channel_name,
        title=req.title or f"Highlight Clip {clip_id_slug}",
        url=url,
        target_format=req.target_format,
        status=ClipStatus.DISCOVERED
    )

    try:
        processed_clip = clip_scheduler.run_single_pipeline_for_clip(
            clip,
            custom_start_sec=req.custom_start_sec,
            custom_end_sec=req.custom_end_sec,
            publish_now=req.auto_upload_youtube
        )
        # VOD parcalandiysa ilk parca bilgisi yayinlanan hedeftir
        processed_path = processed_clip.processed_video_path
        if not processed_path and processed_clip.status == ClipStatus.SPLIT:
            first_part = clip_db.get_clip_by_id(f"{processed_clip.clip_id}_p1")
            if first_part:
                processed_path = first_part.processed_video_path

        return {
            "status": "success",
            "clip_id": processed_clip.clip_id,
            "target_format": processed_clip.target_format.value,
            "raw_video": processed_clip.raw_video_path,
            "processed_video": processed_path,
            "youtube_id": processed_clip.youtube_video_id,
            "clip_status": processed_clip.status.value,
            "message": "Klip başarıyla işlendi."
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Pipeline error: {str(e)}")


# ================= Durum ve Yonetim =================

@router.get("/status", summary="Sistem, Zamanlayıcı, Kota ve Log Durumu")
def get_status():
    settings = clip_db.get_settings()
    logs = clip_scheduler.get_recent_logs(limit=25)
    recent_clips = clip_db.list_clips(limit=10)
    return {
        "scheduler_running": clip_scheduler.is_running,
        "scheduler_status": clip_scheduler.current_status,
        "youtube_ready": shorts_publisher.is_youtube_ready(),
        "quota": clip_publisher.get_quota_summary(settings),
        "recent_logs": logs,
        "recent_clips": recent_clips,
        "settings": settings
    }


@router.post("/scheduler/start", summary="Otonom Zamanlayıcıyı Başlat")
def start_scheduler():
    clip_scheduler.start()
    return {"status": "success", "message": "Zamanlayıcı başlatıldı."}


@router.post("/scheduler/stop", summary="Otonom Zamanlayıcıyı Durdur")
def stop_scheduler():
    clip_scheduler.stop()
    return {"status": "success", "message": "Zamanlayıcı durduruldu."}


@router.get("/settings", response_model=ClipPipelineSettings, summary="Ayarları getir")
def get_settings():
    return clip_db.get_settings()


@router.post("/settings", summary="Ayarları güncelle")
def update_settings(settings: ClipPipelineSettings):
    clip_db.save_settings(settings)
    return {"status": "success", "message": "Ayarlar kaydedildi."}
