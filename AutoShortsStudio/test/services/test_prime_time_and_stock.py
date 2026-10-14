import os
import sys

root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

import unittest
import uuid
from datetime import datetime, timedelta

from app.models.clip_schema import (
    PlatformType, ClipItem, ClipStatus, ClipPipelineSettings, ClipTargetFormat
)
from app.services.clip_db import ClipDatabase
from app.services.video_progress_tracker import video_progress_tracker
from app.services.clip_publisher import ClipPublisherService


class TestPrimeTimeAndStock(unittest.TestCase):
    def setUp(self):
        self.db_name = f"test_pt_{uuid.uuid4().hex[:8]}.db"
        self.db = ClipDatabase(self.db_name)
        self.publisher = ClipPublisherService()

    def tearDown(self):
        if os.path.exists(self.db_name):
            try:
                os.remove(self.db_name)
            except Exception:
                pass

    def test_video_progress_tracker(self):
        """video_progress_tracker servisinin canli veri akisi dogrulanir."""
        clip_id = "test_track_123"
        video_progress_tracker.start_task(
            clip_id=clip_id,
            title="Harika Bir Klip",
            channel_name="test_streamer",
            target_format="shorts",
            platform="youtube",
            duration=45.0
        )

        task = video_progress_tracker.get_active_task()
        self.assertIsNotNone(task)
        self.assertEqual(task["clip_id"], clip_id)
        self.assertEqual(task["stage"], "starting")

        # Indirme ilerlemesi akisi
        video_progress_tracker.update_download_progress(
            percent=50.0,
            speed="5.4 MiB/s",
            eta="00:15",
            detail="Indiriliyor: %50"
        )
        task = video_progress_tracker.get_active_task()
        self.assertEqual(task["stage"], "downloading")
        self.assertAlmostEqual(task["progress_percent"], 17.5, places=1)
        self.assertEqual(task["speed_str"], "5.4 MiB/s")
        self.assertEqual(task["eta_str"], "00:15")

        # Asama guncellemesi (Render)
        video_progress_tracker.update_stage(
            stage="rendering",
            stage_label="9:16 Render",
            percent=75.0,
            detail="Render ediliyor"
        )
        task = video_progress_tracker.get_active_task()
        self.assertEqual(task["stage"], "rendering")
        self.assertEqual(task["progress_percent"], 75.0)

        # Gorev tamamlama
        video_progress_tracker.finish_task(clip_id, success=True, result_path="/path/to/video.mp4")
        self.assertIsNone(video_progress_tracker.get_active_task())

        recent = video_progress_tracker.get_recent_completed()
        self.assertTrue(len(recent) >= 1)
        self.assertEqual(recent[0]["clip_id"], clip_id)
        self.assertTrue(recent[0]["success"])

    def test_prime_time_slot_parsing(self):
        """Prime time slot metninin ayrilmasi dogrulanir."""
        settings = ClipPipelineSettings(prime_time_slots="17:30, 19:45, 21:00")
        slots = self.publisher.parse_prime_time_slots(settings)
        self.assertEqual(slots, [(17, 30), (19, 45), (21, 0)])

    def test_prime_time_detection(self):
        """is_prime_time_now fonksiyonu pencere icinde dogru tespit yapar."""
        settings = ClipPipelineSettings(prime_time_slots="18:00, 20:00, 22:00")
        # 17:55 -> 18:00 slotu icinde
        dt_in_window = datetime(2026, 10, 15, 17, 55)
        is_prime, slot = self.publisher.is_prime_time_now(settings, now=dt_in_window)
        self.assertTrue(is_prime)
        self.assertEqual(slot, "18:00")

        # 14:00 -> slot disinda
        dt_out = datetime(2026, 10, 15, 14, 0)
        is_prime_out, _ = self.publisher.is_prime_time_now(settings, now=dt_out)
        self.assertFalse(is_prime_out)

    def test_scheduled_publish_time_persistence(self):
        """Yeni YouTube filtreleri ve scheduled_publish_time alanlarinin DB'ye yazilip okundugu dogrulanir."""
        sched_time = datetime(2026, 10, 15, 20, 0, 0)
        clip = ClipItem(
            clip_id="test_filter_99",
            platform=PlatformType.YOUTUBE,
            channel_name="rraenee",
            title="Muthis Vurus",
            url="https://youtube.com/watch?v=test",
            target_format=ClipTargetFormat.HORIZONTAL,
            status=ClipStatus.READY,
            youtube_category_id="20",
            made_for_kids=False,
            age_restricted=True,
            custom_thumbnail_path="/storage/thumb.jpg",
            scheduled_publish_time=sched_time,
            priority=25,
            viral_score=9
        )
        self.db.save_clip(clip)

        loaded = self.db.get_clip_by_id("test_filter_99")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.youtube_category_id, "20")
        self.assertFalse(loaded.made_for_kids)
        self.assertTrue(loaded.age_restricted)
        self.assertEqual(loaded.custom_thumbnail_path, "/storage/thumb.jpg")
        self.assertEqual(loaded.scheduled_publish_time, sched_time)
        self.assertEqual(loaded.priority, 25)
        self.assertEqual(loaded.viral_score, 9)

        # list_ready_clips sorgusu
        ready_clips = self.db.list_ready_clips(ClipTargetFormat.HORIZONTAL.value)
        self.assertEqual(len(ready_clips), 1)
        self.assertEqual(ready_clips[0].clip_id, "test_filter_99")

        # update_clip_metadata testi
        self.db.update_clip_metadata(
            "test_filter_99",
            youtube_title="Guncellenmis Baslik",
            made_for_kids=True
        )
        updated = self.db.get_clip_by_id("test_filter_99")
        self.assertEqual(updated.youtube_title, "Guncellenmis Baslik")
        self.assertTrue(updated.made_for_kids)


if __name__ == "__main__":
    unittest.main()
