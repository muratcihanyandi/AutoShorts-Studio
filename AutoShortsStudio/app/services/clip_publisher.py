from datetime import datetime, timedelta, time as dtime
from typing import Dict, Any, Optional, List, Tuple
from loguru import logger

from app.models.clip_schema import ClipPipelineSettings, ClipTargetFormat, ClipItem, ClipStatus
from app.services.clip_db import clip_db
from app.services.shorts_publisher import shorts_publisher


class ClipPublisherService:
    """
    Gunluk kota, Prime Time slotlari ve akilli zamanlama ile sinirli YouTube yayinlayicisi.
    Stoktaki (READY) videolari format bazinda gunde N adet ile sinirli olarak,
    izlenmelerin tavan yaptigi Prime Time saatlerinde (orn. 18:00, 20:00, 22:00) yayinlar.
    Her videonun ne zaman yayinlanacagini dinamik hesaplar.
    """

    def daily_limit(self, target_format: ClipTargetFormat, settings: ClipPipelineSettings) -> int:
        if target_format == ClipTargetFormat.SHORTS:
            return max(0, settings.daily_shorts_limit)
        return max(0, settings.daily_horizontal_limit)

    def target_stock(self, target_format: ClipTargetFormat, settings: ClipPipelineSettings) -> int:
        if target_format == ClipTargetFormat.SHORTS:
            return max(1, settings.target_stock_shorts)
        return max(1, settings.target_stock_horizontal)

    def published_today(self, target_format: ClipTargetFormat) -> int:
        return clip_db.published_today_count(target_format.value)

    def quota_remaining(self, target_format: ClipTargetFormat, settings: ClipPipelineSettings) -> int:
        return max(0, self.daily_limit(target_format, settings) - self.published_today(target_format))

    def parse_prime_time_slots(self, settings: ClipPipelineSettings) -> List[Tuple[int, int]]:
        """Ayar metnindeki (orn: '18:00, 20:00, 22:00') saatleri (saat, dakika) tuple listesine cevirir."""
        raw = settings.prime_time_slots or "18:00, 20:00, 22:00"
        slots: List[Tuple[int, int]] = []
        for part in raw.split(","):
            part = part.strip()
            if ":" in part:
                try:
                    h, m = part.split(":", 1)
                    slots.append((int(h), int(m)))
                except Exception:
                    continue
        if not slots:
            slots = [(18, 0), (20, 0), (22, 0)]
        slots.sort()
        return slots

    def is_within_publish_window(self, settings: ClipPipelineSettings, now: Optional[datetime] = None) -> bool:
        """Yuklemenin izinli oldugu yerel saat araliginda miyiz?"""
        hour = (now or datetime.now()).hour
        start = settings.publish_window_start_hour
        end = settings.publish_window_end_hour
        if start <= end:
            return start <= hour < end
        # Geceyi saran pencere (orn. 22 - 02)
        return hour >= start or hour < end

    def is_prime_time_now(
        self,
        settings: ClipPipelineSettings,
        now: Optional[datetime] = None,
        window_minutes: int = 45
    ) -> Tuple[bool, Optional[str]]:
        """
        Su anki saat bir Prime Time slotu araliginda mi?
        Ornegin slot 18:00 ise 17:50 - 18:45 arasinda True doner.
        """
        dt = now or datetime.now()
        cur_min_of_day = dt.hour * 60 + dt.minute

        for h, m in self.parse_prime_time_slots(settings):
            slot_min = h * 60 + m
            if (slot_min - 10) <= cur_min_of_day <= (slot_min + window_minutes):
                return True, f"{h:02d}:{m:02d}"
        return False, None

    def calculate_schedule_for_stock(
        self,
        target_format: ClipTargetFormat,
        settings: ClipPipelineSettings,
        now: Optional[datetime] = None
    ) -> List[Tuple[ClipItem, datetime, str]]:
        """
        Stoktaki tum READY videolari viral puanina ve onceligine gore siralayip
        gelecekteki Prime Time slotlarina esler.
        Dondurur: [(clip, scheduled_datetime, display_label), ...]
        """
        ready_clips = clip_db.list_ready_clips(target_format.value, limit=50)
        if not ready_clips:
            return []

        dt = now or datetime.now()
        slots = self.parse_prime_time_slots(settings)
        published_today = self.published_today(target_format)
        daily_lim = self.daily_limit(target_format, settings)

        # Bugunun henuz gecmemis slotlarini bul
        remaining_today_slots: List[Tuple[int, int]] = []
        cur_min = dt.hour * 60 + dt.minute
        for h, m in slots:
            slot_min = h * 60 + m
            # Slot henuz gecmediyse ve gunluk limit asilmadiysa
            if slot_min >= cur_min - 15 and len(remaining_today_slots) + published_today < daily_lim:
                remaining_today_slots.append((h, m))

        scheduled_pairs: List[Tuple[ClipItem, datetime, str]] = []
        current_day_offset = 0

        # Ilk parti bugunun kalan slotlarina atanir
        slot_queue: List[Tuple[int, Tuple[int, int]]] = []
        for s in remaining_today_slots:
            slot_queue.append((0, s))

        # Sonraki gunler icin slot havuzu genisletilir
        day = 1
        while len(slot_queue) < len(ready_clips) + 10:
            for s in slots[:daily_lim]:
                slot_queue.append((day, s))
            day += 1

        for idx, clip in enumerate(ready_clips):
            if idx < len(slot_queue):
                day_offset, (sh, sm) = slot_queue[idx]
                target_date = (dt + timedelta(days=day_offset)).replace(
                    hour=sh, minute=sm, second=0, microsecond=0
                )
                if day_offset == 0:
                    label = f"Bugün {sh:02d}:{sm:02d} (Prime Time)"
                elif day_offset == 1:
                    label = f"Yarın {sh:02d}:{sm:02d} (Prime Time)"
                else:
                    label = f"{target_date.strftime('%d.%m')} {sh:02d}:{sm:02d} (Prime Time)"
            else:
                target_date = dt + timedelta(days=idx)
                label = f"{target_date.strftime('%d.%m')} (Stokta Sırada)"

            scheduled_pairs.append((clip, target_date, label))

        return scheduled_pairs

    def update_scheduled_times_in_db(self, settings: ClipPipelineSettings):
        """Stoktaki videolara hesaplanan planlanmis yayin zamanlarini yazar."""
        for fmt in ClipTargetFormat:
            pairs = self.calculate_schedule_for_stock(fmt, settings)
            for clip, sched_dt, label in pairs:
                if clip.scheduled_publish_time != sched_dt:
                    clip.scheduled_publish_time = sched_dt
                    clip_db.update_clip_metadata(clip.clip_id, scheduled_publish_time=sched_dt)

    def get_quota_summary(self, settings: ClipPipelineSettings) -> Dict[str, Any]:
        summary = {}
        for fmt in ClipTargetFormat:
            published = self.published_today(fmt)
            limit = self.daily_limit(fmt, settings)
            target_stk = self.target_stock(fmt, settings)
            ready_stk = clip_db.count_clips(status="ready", target_format=fmt.value)
            discovered_stk = clip_db.count_clips(status="discovered", target_format=fmt.value)
            downloaded_stk = clip_db.count_clips(status="downloaded", target_format=fmt.value)
            summary[fmt.value] = {
                "published_today": published,
                "daily_limit": limit,
                "remaining": max(0, limit - published),
                "target_stock": target_stk,
                "ready_in_stock": ready_stk,
                "downloaded_in_stock": downloaded_stk,
                "discovered_in_stock": discovered_stk,
                "stock_full": ready_stk >= target_stk,
            }
        return summary

    def publish_next_ready(
        self,
        target_format: ClipTargetFormat,
        settings: ClipPipelineSettings
    ) -> Optional[Dict[str, Any]]:
        """Stoktaki siradaki en yuksek viraliteli READY videoyu yukler."""
        if not shorts_publisher.is_youtube_ready():
            logger.warning("YouTube token hazir degil, yayinlama atlandi.")
            return None

        clip = clip_db.next_ready_clip(target_format.value)
        if clip is None:
            return None

        video_id = shorts_publisher.publish_clip(clip, settings=settings)
        return {
            "clip_id": clip.clip_id,
            "title": clip.title,
            "youtube_video_id": video_id,
        }

    def run_publish_tick(self, settings: ClipPipelineSettings) -> Dict[str, Any]:
        """
        Zamanlayici tarafindan periyodik cagrilir.
        Her format icin:
        - Stoktaki planlanmis saatleri gunceller.
        - Kota var mi + yayin penceresinde / Prime Time'da miyiz + stokta READY var mi kontrol eder.
        - Uygunsa en yuksek oncelikli videoyu yukler.
        """
        result: Dict[str, Any] = {"published": [], "skipped": {}}

        # Oncelikle stok zamanlamasini guncelle
        try:
            self.update_scheduled_times_in_db(settings)
        except Exception as ex:
            logger.debug(f"Failed to update scheduled publish times: {ex}")

        if not settings.auto_upload:
            result["skipped"]["reason"] = "auto_upload kapali"
            return result

        if not self.is_within_publish_window(settings):
            result["skipped"]["reason"] = (
                f"yayin penceresi disinda ({settings.publish_window_start_hour}:00 - "
                f"{settings.publish_window_end_hour}:00)"
            )
            return result

        is_prime, slot_name = self.is_prime_time_now(settings)

        for fmt in ClipTargetFormat:
            remaining = self.quota_remaining(fmt, settings)
            if remaining <= 0:
                result["skipped"][fmt.value] = "gunluk kota dolu (3/3 tamamlandi)"
                continue

            # Prime Time kontrolu: Eğer oto-yükleme açıksa ve şu an Prime Time slotu içindeysek kesinlikle yayınla
            published_info = self.publish_next_ready(fmt, settings)
            if published_info:
                result["published"].append(published_info)
                slot_info = f" [{slot_name} Prime Time]" if is_prime else ""
                logger.info(
                    f"[Publisher] {fmt.value}{slot_info} yuklendi: {published_info['title']} "
                    f"(https://youtu.be/{published_info['youtube_video_id']})"
                )
            else:
                result["skipped"][fmt.value] = "stokta READY video yok veya YouTube hazir degil"

        return result


# Singleton ornek
clip_publisher = ClipPublisherService()
