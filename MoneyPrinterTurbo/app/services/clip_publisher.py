from datetime import datetime
from typing import Dict, Any, Optional
from loguru import logger

from app.models.clip_schema import ClipPipelineSettings, ClipTargetFormat
from app.services.clip_db import clip_db
from app.services.shorts_publisher import shorts_publisher


class ClipPublisherService:
    """
    Gunluk kota ile sinirli YouTube yayinlayicisi.
    Stoktaki (READY) videolari format bazinda gunde N adet ile sinirli olarak,
    belirli saat araliginda yayinlar. Kalanlar stokta bekler.
    """

    def daily_limit(self, target_format: ClipTargetFormat, settings: ClipPipelineSettings) -> int:
        if target_format == ClipTargetFormat.SHORTS:
            return max(0, settings.daily_shorts_limit)
        return max(0, settings.daily_horizontal_limit)

    def published_today(self, target_format: ClipTargetFormat) -> int:
        return clip_db.published_today_count(target_format.value)

    def quota_remaining(self, target_format: ClipTargetFormat, settings: ClipPipelineSettings) -> int:
        return max(0, self.daily_limit(target_format, settings) - self.published_today(target_format))

    def is_within_publish_window(self, settings: ClipPipelineSettings, now: Optional[datetime] = None) -> bool:
        """Yuklemenin izinli oldugu yerel saat araliginda miyiz?"""
        hour = (now or datetime.now()).hour
        start = settings.publish_window_start_hour
        end = settings.publish_window_end_hour
        if start <= end:
            return start <= hour < end
        # Geceyi saran pencere (orn. 22 - 02)
        return hour >= start or hour < end

    def get_quota_summary(self, settings: ClipPipelineSettings) -> Dict[str, Any]:
        summary = {}
        for fmt in ClipTargetFormat:
            published = self.published_today(fmt)
            limit = self.daily_limit(fmt, settings)
            summary[fmt.value] = {
                "published_today": published,
                "daily_limit": limit,
                "remaining": max(0, limit - published),
                "ready_in_stock": clip_db.count_clips(status="ready", target_format=fmt.value),
                "discovered_in_stock": clip_db.count_clips(status="discovered", target_format=fmt.value),
            }
        return summary

    def publish_next_ready(
        self,
        target_format: ClipTargetFormat,
        settings: ClipPipelineSettings
    ) -> Optional[Dict[str, Any]]:
        """Stoktaki siradaki READY videoyu yukler. Yuklenen video bilgisini dondurur."""
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
        Zamanlayici tarafindan periyodik cagrilir. Her format icin:
        kota var mi + yayin penceresinde miyiz + stokta READY var mi -> bir video yukle.
        """
        result: Dict[str, Any] = {"published": [], "skipped": {}}

        if not settings.auto_upload:
            result["skipped"]["reason"] = "auto_upload kapali"
            return result

        if not self.is_within_publish_window(settings):
            result["skipped"]["reason"] = (
                f"yayin penceresi disinda ({settings.publish_window_start_hour}:00 - "
                f"{settings.publish_window_end_hour}:00)"
            )
            return result

        for fmt in ClipTargetFormat:
            remaining = self.quota_remaining(fmt, settings)
            if remaining <= 0:
                result["skipped"][fmt.value] = "gunluk kota dolu"
                continue

            published_info = self.publish_next_ready(fmt, settings)
            if published_info:
                result["published"].append(published_info)
                logger.info(
                    f"[Publisher] {fmt.value} yuklendi: {published_info['title']} "
                    f"(https://youtu.be/{published_info['youtube_video_id']})"
                )
            else:
                result["skipped"][fmt.value] = "stokta READY video yok veya YouTube hazir degil"

        return result


# Singleton ornek
clip_publisher = ClipPublisherService()
