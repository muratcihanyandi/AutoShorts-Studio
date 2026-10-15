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

    def test_upload_filters_and_fallback(self):
        """Ayar seviyesi fallback ve age_restricted YouTube API'ye iletilir (S5, S6 dogrulamasi)."""
        from app.services.shorts_publisher import shorts_publisher
        from unittest.mock import patch, MagicMock

        settings = ClipPipelineSettings(made_for_kids=True, age_restricted=True)
        clip = ClipItem(
            clip_id="test_filter_fallback",
            platform=PlatformType.YOUTUBE,
            channel_name="rraenee",
            title="Video",
            url="https://youtube.com/watch?v=123",
            target_format=ClipTargetFormat.SHORTS,
            status=ClipStatus.READY,
            processed_video_path=os.path.abspath(__file__), # Mevcut gercek dosya
            made_for_kids=None,
            age_restricted=None,
        )

        with patch("app.services.shorts_publisher.upload_to_youtube", return_value="yt_vid_456") as mock_upload, \
             patch("app.services.shorts_publisher.clip_db", self.db):
            self.db.save_clip(clip)
            vid = shorts_publisher.publish_clip_to_shorts(clip, settings=settings)
            self.assertEqual(vid, "yt_vid_456")

            # upload_to_youtube cagrildi mi ve dogru filtreler (daima False) gitti mi?
            mock_upload.assert_called_once()
            kwargs = mock_upload.call_args.kwargs
            self.assertFalse(kwargs["made_for_kids"], "made_for_kids daima False olmali")
            self.assertFalse(kwargs["age_restricted"], "age_restricted daima False olmali")

    def test_missing_file_marks_failed_no_head_of_line_blocking(self):
        """Dosya diskte yoksa klip READY kalmamali, FAILED yapilmali (S4 dogrulamasi)."""
        from app.services.shorts_publisher import shorts_publisher
        from unittest.mock import patch

        clip = ClipItem(
            clip_id="test_missing_file_clip",
            platform=PlatformType.YOUTUBE,
            channel_name="rraenee",
            title="Missing Video",
            url="https://youtube.com/watch?v=999",
            target_format=ClipTargetFormat.SHORTS,
            status=ClipStatus.READY,
            processed_video_path="/non/existent/path/never_there.mp4",
        )
        self.db.save_clip(clip)

        with patch("app.services.shorts_publisher.clip_db", self.db):
            with self.assertRaises(FileNotFoundError):
                shorts_publisher.publish_clip_to_shorts(clip)

            # Klip READY kalmamali, FAILED olarak guncellenmis olmali
            saved = self.db.get_clip_by_id("test_missing_file_clip")
            self.assertEqual(saved.status, ClipStatus.FAILED)
            self.assertIn("Processed video not found", saved.error_message)

    def test_k1_slot_uniqueness_guarantee(self):
        """Ayni slot penceresi icinde birden fazla video yuklenmesi onlenmeli (K1 dogrulamasi)."""
        from unittest.mock import patch
        settings = ClipPipelineSettings(
            enforce_prime_time=True,
            prime_time_slots="18:00, 20:00, 22:00",
            auto_upload=True
        )
        dt_18_00 = datetime(2026, 10, 15, 18, 0)
        with patch("app.services.clip_publisher.clip_db", self.db):
            self.assertFalse(self.publisher.is_slot_published_today(ClipTargetFormat.SHORTS, "18:00", settings, now=dt_18_00))

            clip = ClipItem(
                clip_id="c_18",
                platform=PlatformType.YOUTUBE,
                channel_name="ch",
                title="V18",
                url="u",
                target_format=ClipTargetFormat.SHORTS,
                status=ClipStatus.PUBLISHED,
                published_at=dt_18_00
            )
            self.db.save_clip(clip)

            dt_18_30 = datetime(2026, 10, 15, 18, 30)
            self.assertTrue(self.publisher.is_slot_published_today(ClipTargetFormat.SHORTS, "18:00", settings, now=dt_18_30))

            dt_20_00 = datetime(2026, 10, 15, 20, 0)
            self.assertFalse(self.publisher.is_slot_published_today(ClipTargetFormat.SHORTS, "20:00", settings, now=dt_20_00))

    def test_k2_empty_slot_string_still_enforces_prime_time(self):
        """Bos prime_time_slots girilse bile varsayilan slotlarla Prime Time denetlenir (K2 dogrulamasi)."""
        from unittest.mock import patch
        settings = ClipPipelineSettings(
            enforce_prime_time=True,
            prime_time_slots="",
            auto_upload=True
        )
        dt_noon = datetime(2026, 10, 15, 14, 0)
        with patch("app.services.clip_publisher.clip_db", self.db):
            res = self.publisher.run_publish_tick(settings, now=dt_noon)
            self.assertIn("Prime Time disinda", res["skipped"].get("reason", ""))

    def test_k3_db_roundtrip_preserves_none_and_applies_settings(self):
        """DB'ye None olarak kaydedilen filtreler okundugunda None kalmali ve ayar fallback'i calismali (K3)."""
        from app.services.shorts_publisher import shorts_publisher
        from unittest.mock import patch
        clip = ClipItem(
            clip_id="test_k3_roundtrip",
            platform=PlatformType.YOUTUBE,
            channel_name="rraenee",
            title="K3 Test",
            url="https://youtube.com/watch?v=k3",
            target_format=ClipTargetFormat.SHORTS,
            status=ClipStatus.READY,
            made_for_kids=None,
            age_restricted=None,
        )
        self.db.save_clip(clip)
        loaded = self.db.get_clip_by_id("test_k3_roundtrip")
        self.assertFalse(loaded.made_for_kids, "DB'den okunan made_for_kids False olmali")
        self.assertFalse(loaded.age_restricted, "DB'den okunan age_restricted False olmali")

        settings = ClipPipelineSettings(made_for_kids=True, age_restricted=True)
        with patch("app.services.shorts_publisher.upload_to_youtube", return_value="yt_vid_k3") as mock_upload, \
             patch("app.services.shorts_publisher.clip_db", self.db):
            loaded.processed_video_path = os.path.abspath(__file__)
            shorts_publisher.publish_clip_to_shorts(loaded, settings=settings)
            mock_upload.assert_called_once()
            kwargs = mock_upload.call_args.kwargs
            self.assertFalse(kwargs["made_for_kids"], "YouTube yuklemesinde made_for_kids daima False olmali")
            self.assertFalse(kwargs["age_restricted"], "YouTube yuklemesinde age_restricted daima False olmali")

    def test_k4_two_pointer_sliding_window_intro_contamination_prevented(self):
        """Gurultulu intro senaryosunda kayan pencere kontaminasyonsuz dogru pencereyi secmeli (K4)."""
        from app.services.hype_detector import hype_detector
        from unittest.mock import patch, MagicMock

        lines = []
        for i in range(80):
            t = i * 0.5
            m = 20.0 if t < 10 else (-30.0 if t < 25 else -60.0)
            lines.append(f"[Parsed_ebur128] t: {t:.2f} M: {m:.1f}")
        fake_stderr = "\n".join(lines)

        mock_proc = MagicMock(returncode=0, stderr=fake_stderr)
        with patch("subprocess.run", return_value=mock_proc), \
             patch.object(hype_detector, "get_media_duration", return_value=40.0):
            start, end, score = hype_detector.analyze_audio_volume_peaks(
                "dummy.mp4", target_window_sec=10.0, intro_padding_sec=10.0, outro_padding_sec=0.0
            )
            self.assertEqual(start, 10.0)
            self.assertEqual(end, 20.0)


if __name__ == "__main__":
    unittest.main()
