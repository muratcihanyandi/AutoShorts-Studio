import os
import sys
import unittest
import uuid
from datetime import datetime, timedelta

root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from app.models.clip_schema import (
    ClipPipelineSettings, ClipItem, ClipStatus, ClipTargetFormat, PlatformType, ClipSourceKind
)
from app.services.clip_downloader import clip_downloader, ClipDownloaderService
from app.services.clip_db import ClipDatabase


class TestViralHunterService(unittest.TestCase):
    def setUp(self):
        self.db_name = f"test_vh_{uuid.uuid4().hex[:8]}.db"
        self.db = ClipDatabase(self.db_name)

    def tearDown(self):
        if os.path.exists(self.db_name):
            try:
                os.remove(self.db_name)
            except Exception:
                pass

    def test_viral_hunter_toggle_default_is_false(self):
        """Viral video avi varsayilan olarak False olmali, sadece guncel videolari secmeli."""
        settings = ClipPipelineSettings()
        self.assertFalse(settings.enable_viral_hunter)
        self.assertEqual(settings.max_video_age_days, 7)

    def test_viral_hunter_off_rejects_300k_and_500k_old_videos(self):
        """Viral av kapaliyken, 300k ve 500k izlenmis eski videolar KESINLIKLE elenmeli."""
        old_date = (datetime.utcnow() - timedelta(days=60)).strftime("%Y%m%d")

        # 300.000 izlenmis 60 gunluk video -> Red
        self.assertFalse(ClipDownloaderService._is_video_fresh_or_viral(
            {"upload_date": old_date},
            view_count=300000,
            max_age_days=7,
            viral_threshold=50000,
            enable_viral_hunter=False
        ))

        # 500.000 izlenmis 60 gunluk video -> Red
        self.assertFalse(ClipDownloaderService._is_video_fresh_or_viral(
            {"upload_date": old_date},
            view_count=500000,
            max_age_days=7,
            viral_threshold=50000,
            enable_viral_hunter=False
        ))

    def test_viral_hunter_on_accepts_high_view_older_video(self):
        """Viral av acikken, baraji gecen eski viral videolar kabul edilmeli."""
        old_date = (datetime.utcnow() - timedelta(days=60)).strftime("%Y%m%d")
        self.assertTrue(ClipDownloaderService._is_video_fresh_or_viral(
            {"upload_date": old_date},
            view_count=300000,
            max_age_days=7,
            viral_threshold=50000,
            enable_viral_hunter=True
        ))

    def test_fresh_videos_always_accepted_regardless_of_views(self):
        """Son max_age_days icindeki taze videolar az izlenmis olsa bile kabul edilmeli."""
        fresh_date = (datetime.utcnow() - timedelta(days=2)).strftime("%Y%m%d")
        self.assertTrue(ClipDownloaderService._is_video_fresh_or_viral(
            {"upload_date": fresh_date},
            view_count=150,
            max_age_days=7,
            viral_threshold=50000,
            enable_viral_hunter=False
        ))

    def test_kick_created_at_iso_format_parsed_correctly(self):
        """Kick API'den gelen ISO tarih formati yas tespitinde dogru islenmeli."""
        old_kick_date = (datetime.utcnow() - timedelta(days=45)).strftime("%Y-%m-%d %H:%M:%S")
        self.assertFalse(ClipDownloaderService._is_video_fresh_or_viral(
            {"created_at": old_kick_date},
            view_count=450000,
            max_age_days=7,
            viral_threshold=50000,
            enable_viral_hunter=False
        ))

    def test_clean_stale_discovered_clips_in_database(self):
        """clean_stale_discovered_clips eski kesif kliplerini SKIPPED olarak temizlemeli."""
        old_dt = (datetime.utcnow() - timedelta(days=30)).isoformat()
        fresh_dt = (datetime.utcnow() - timedelta(days=2)).isoformat()

        clip_old = ClipItem(
            clip_id="old_viral_500k",
            source_key="old_viral_500k",
            platform=PlatformType.YOUTUBE,
            channel_name="test",
            title="Eski Viral 500k",
            url="https://youtube.com/watch?v=old",
            view_count=500000,
            created_at_platform=old_dt,
            status=ClipStatus.DISCOVERED,
            target_format=ClipTargetFormat.HORIZONTAL
        )
        clip_fresh = ClipItem(
            clip_id="fresh_5k",
            source_key="fresh_5k",
            platform=PlatformType.YOUTUBE,
            channel_name="test",
            title="Taze Video 5k",
            url="https://youtube.com/watch?v=fresh",
            view_count=5000,
            created_at_platform=fresh_dt,
            status=ClipStatus.DISCOVERED,
            target_format=ClipTargetFormat.HORIZONTAL
        )
        self.db.save_clip(clip_old)
        self.db.save_clip(clip_fresh)

        # Viral avi kapaliyken temizle (7 gun limiti)
        cleaned = self.db.clean_stale_discovered_clips(max_age_days=7, enable_viral_hunter=False)
        self.assertEqual(cleaned, 1)

        # Eski klip SKIPPED olmali, taze klip DISCOVERED kalmali
        old_res = self.db.get_clip_by_id("old_viral_500k")
        fresh_res = self.db.get_clip_by_id("fresh_5k")
        self.assertEqual(old_res.status, ClipStatus.SKIPPED)
        self.assertEqual(fresh_res.status, ClipStatus.DISCOVERED)

    def test_next_discovered_clip_skips_old_clips_when_hunter_off(self):
        """next_discovered_clip ayarlar verildiginde ve viral av kapaliyken eski klibi atlayip taze klibi secmeli."""
        old_dt = (datetime.utcnow() - timedelta(days=40)).isoformat()
        fresh_dt = (datetime.utcnow() - timedelta(days=1)).isoformat()

        clip_old = ClipItem(
            clip_id="old_top_views",
            source_key="old_top_views",
            platform=PlatformType.YOUTUBE,
            channel_name="test",
            title="Eski 1M Izlenme",
            url="https://youtube.com/watch?v=old1",
            view_count=1000000, # view_count cok yuksek
            created_at_platform=old_dt,
            status=ClipStatus.DISCOVERED,
            target_format=ClipTargetFormat.HORIZONTAL
        )
        clip_fresh = ClipItem(
            clip_id="fresh_normal_views",
            source_key="fresh_normal_views",
            platform=PlatformType.YOUTUBE,
            channel_name="test",
            title="Dun Yayinlanan Video",
            url="https://youtube.com/watch?v=fresh1",
            view_count=2000,    # view_count dusuk ama taze
            created_at_platform=fresh_dt,
            status=ClipStatus.DISCOVERED,
            target_format=ClipTargetFormat.HORIZONTAL
        )
        self.db.save_clip(clip_old)
        self.db.save_clip(clip_fresh)

        settings = ClipPipelineSettings(enable_viral_hunter=False, max_video_age_days=7)

        # next_discovered_clip eski klibi SKIPPED yapip dogrudan taze olan clip_fresh'i donmeli
        next_clip = self.db.next_discovered_clip(ClipTargetFormat.HORIZONTAL.value, settings=settings)
        self.assertIsNotNone(next_clip)
        self.assertEqual(next_clip.clip_id, "fresh_normal_views")
        self.assertEqual(next_clip.title, "Dun Yayinlanan Video")


if __name__ == "__main__":
    unittest.main()
