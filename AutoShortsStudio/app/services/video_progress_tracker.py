import time
import threading
from typing import Optional, Dict, Any, List
from datetime import datetime
from loguru import logger


class VideoProgressTrackerService:
    """
    Video basina canli indirme, analiz, kurgu ve yukleme ilerlemesini
    thread-safe olarak takip eden servis.
    WebUI ve zamanlayici tarafindan anlik olarak okunur.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._active_task: Optional[Dict[str, Any]] = None
        self._recent_completed: List[Dict[str, Any]] = []

    def start_task(
        self,
        clip_id: str,
        title: str,
        channel_name: str,
        target_format: str,
        platform: str = "youtube",
        thumbnail_url: Optional[str] = None,
        duration: float = 0.0,
    ):
        """Yeni bir videonun islem hattini baslatir."""
        with self._lock:
            self._active_task = {
                "clip_id": clip_id,
                "title": title,
                "channel_name": channel_name,
                "target_format": target_format,
                "platform": platform,
                "thumbnail_url": thumbnail_url,
                "duration": duration,
                "stage": "starting",
                "stage_label": "Başlatılıyor",
                "progress_percent": 0.0,
                "speed_str": "",
                "eta_str": "",
                "elapsed_str": "00:00",
                "downloaded_bytes": 0,
                "total_bytes": 0,
                "detail_message": "İşlem kuyruğa alındı...",
                "start_time": time.time(),
                "updated_at": time.time(),
            }
            logger.debug(f"[ProgressTracker] Task started: {clip_id} ({title})")

    def update_download_progress(
        self,
        percent: float,
        speed: str = "",
        eta: str = "",
        downloaded_bytes: int = 0,
        total_bytes: int = 0,
        detail: str = ""
    ):
        """yt-dlp indirme ilerlemesini (0% - 35% arasina olceklenmis) gunceller."""
        with self._lock:
            if not self._active_task:
                return
            elapsed_sec = int(time.time() - self._active_task["start_time"])
            mins, secs = divmod(elapsed_sec, 60)
            elapsed_str = f"{mins:02d}:{secs:02d}"

            # Download asamasi genel islem hattinin %0 - %35'i araliginda temsil edilir
            capped_percent = max(0.0, min(100.0, percent))
            overall_percent = round(capped_percent * 0.35, 1)

            self._active_task.update({
                "stage": "downloading",
                "stage_label": f"İndiriliyor (%{capped_percent:.1f})",
                "progress_percent": overall_percent,
                "speed_str": speed,
                "eta_str": eta,
                "elapsed_str": elapsed_str,
                "downloaded_bytes": downloaded_bytes,
                "total_bytes": total_bytes,
                "detail_message": detail or f"İndiriliyor: %{capped_percent:.1f} ({speed}) - Kalan: {eta}",
                "updated_at": time.time(),
            })

    def update_stage(
        self,
        stage: str,
        stage_label: str,
        percent: float,
        detail: str = "",
        eta_str: str = ""
    ):
        """Analiz, kurgu, render, altyazi veya yukleme adimini gunceller."""
        with self._lock:
            if not self._active_task:
                return
            elapsed_sec = int(time.time() - self._active_task["start_time"])
            mins, secs = divmod(elapsed_sec, 60)
            elapsed_str = f"{mins:02d}:{secs:02d}"

            self._active_task.update({
                "stage": stage,
                "stage_label": stage_label,
                "progress_percent": round(max(0.0, min(100.0, percent)), 1),
                "detail_message": detail,
                "eta_str": eta_str or self._active_task.get("eta_str", ""),
                "elapsed_str": elapsed_str,
                "updated_at": time.time(),
            })

    def finish_task(self, clip_id: str, success: bool = True, error: str = "", result_path: str = ""):
        """Aktif gorevi sonlandirir ve gecmis listesine ekler."""
        with self._lock:
            if self._active_task and self._active_task.get("clip_id") == clip_id:
                task = dict(self._active_task)
                elapsed_sec = int(time.time() - task["start_time"])
                mins, secs = divmod(elapsed_sec, 60)
                task["elapsed_str"] = f"{mins:02d}:{secs:02d}"
                task["success"] = success
                task["error"] = error
                task["result_path"] = result_path
                task["finished_at"] = datetime.now().strftime("%H:%M:%S")

                if success:
                    task["stage"] = "completed"
                    task["stage_label"] = "Tamamlandı"
                    task["progress_percent"] = 100.0
                    task["detail_message"] = "İşlem başarıyla tamamlandı!"
                else:
                    task["stage"] = "failed"
                    task["stage_label"] = "Başarısız"
                    task["detail_message"] = f"Hata: {error}"

                self._recent_completed.insert(0, task)
                if len(self._recent_completed) > 10:
                    self._recent_completed.pop()

                self._active_task = None
                logger.debug(f"[ProgressTracker] Task finished: {clip_id} (success={success})")

    def get_active_task(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            if not self._active_task:
                return None
            data = dict(self._active_task)
            elapsed_sec = int(time.time() - data["start_time"])
            mins, secs = divmod(elapsed_sec, 60)
            data["elapsed_str"] = f"{mins:02d}:{secs:02d}"
            return data

    def get_recent_completed(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._recent_completed)

    def is_busy(self) -> bool:
        with self._lock:
            return self._active_task is not None


# Singleton ornek
video_progress_tracker = VideoProgressTrackerService()
