import os
import sys
import uuid

root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

import unittest
from unittest.mock import patch

from app.models.clip_schema import (
    PlatformType, ClipItem, ClipStatus, StreamerTarget, ClipPipelineSettings,
    ClipTargetFormat,
)
from app.services.clip_db import ClipDatabase
from app.services.clip_scheduler import clip_scheduler
from app.services.clip_downloader import clip_downloader
from app.services.shorts_publisher import shorts_publisher
from app.services import clip_editor


class TestSchedulerFlow(unittest.TestCase):
    """
    Scheduler'in 3 asamali dongusunu (kesif -> stok isleme -> kotali yayinlama)
    ag ve ffmpeg olmadan dogrular. Tum servislerin clip_db referanslari gecici
    test veritabanina yonlendirilir.
    """

    def setUp(self):
        self.db_path = f"test_sched_{uuid.uuid4().hex[:8]}.db"
        self.db = ClipDatabase(db_path=self.db_path)
        self.ready_dir = os.path.join(root_dir, "storage", "clips", "ready")
        self.raw_dir = os.path.join(root_dir, "storage", "clips", "raw")
        os.makedirs(self.ready_dir, exist_ok=True)
        os.makedirs(self.raw_dir, exist_ok=True)
        self.created_files = []

        self._db_patches = [
            patch("app.services.clip_scheduler.clip_db", self.db),
            patch("app.services.clip_publisher.clip_db", self.db),
            patch("app.services.shorts_publisher.clip_db", self.db),
            patch("app.services.clip_downloader.clip_db", self.db),
        ]
        for p in self._db_patches:
            p.start()

        # Scheduler fazlari dogrudan cagriliyor; baska bir testten kalan
        # durdurma sinyali ic donguleri sessizce kirar, temizle.
        clip_scheduler._stop_event.clear()

    def tearDown(self):
        for p in self._db_patches:
            p.stop()
        for f in self.created_files:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except Exception:
                    pass
        if os.path.exists(self.db_path):
            try:
                os.remove(self.db_path)
            except Exception:
                pass

    def _make_file(self, path: str, size: int = 4096) -> str:
        with open(path, "wb") as f:
            f.write(b"\x00" * size)
        self.created_files.append(path)
        return path

    def test_discover_writes_to_stock(self):
        """Kesif, bulunan tum icerikleri stoga (DISCOVERED) yazmali."""
        streamer = StreamerTarget(
            platform=PlatformType.TWITCH,
            channel_name="flowtest",
            formats=[ClipTargetFormat.SHORTS, ClipTargetFormat.HORIZONTAL],
            check_interval_minutes=60,
        )
        self.db.add_streamer(streamer)

        fake_items = [
            ClipItem(
                clip_id="flow_shorts_1", source_key="flow_shorts_1",
                platform=PlatformType.TWITCH, channel_name="flowtest",
                title="Shorts item", url="https://clips.twitch.tv/s1",
                target_format=ClipTargetFormat.SHORTS, status=ClipStatus.DISCOVERED,
            ),
            ClipItem(
                clip_id="flow_horiz_1", source_key="flow_horiz_1",
                platform=PlatformType.TWITCH, channel_name="flowtest",
                title="Horizontal item", url="https://clips.twitch.tv/h1",
                target_format=ClipTargetFormat.HORIZONTAL, status=ClipStatus.DISCOVERED,
            ),
        ]
        settings = ClipPipelineSettings()

        with patch.object(clip_downloader, "fetch_streamer_feed", return_value=fake_items):
            clip_scheduler._discover_all(settings)

        self.assertIsNotNone(self.db.get_clip_by_id("flow_shorts_1"))
        self.assertIsNotNone(self.db.get_clip_by_id("flow_horiz_1"))
        self.assertEqual(self.db.count_clips(status="discovered"), 2)

    def test_discover_skips_duplicates(self):
        """Zaten stokta olan icerik tekrar eklenmemeli (dedupe)."""
        streamer = StreamerTarget(
            platform=PlatformType.TWITCH, channel_name="dupetest",
            formats=[ClipTargetFormat.SHORTS], check_interval_minutes=60,
        )
        self.db.add_streamer(streamer)

        existing = ClipItem(
            clip_id="dup_1", source_key="dup_1", platform=PlatformType.TWITCH,
            channel_name="dupetest", title="Already in stock", url="u",
            target_format=ClipTargetFormat.SHORTS, status=ClipStatus.DISCOVERED,
        )
        self.db.save_clip(existing)

        settings = ClipPipelineSettings()
        with patch.object(clip_downloader, "fetch_streamer_feed", return_value=[existing]):
            clip_scheduler._discover_all(settings)

        # Sayi artmamali (tek kayit)
        self.assertEqual(self.db.count_clips(status="discovered"), 1)

    def test_stock_processing_and_quota_publish(self):
        """DISCOVERED oge islenip READY olmali; kota tick'i stoktan bir video yuklemeli."""
        settings = ClipPipelineSettings(
            auto_upload=True,
            ai_title_generation=False,  # LLM cagrisi yapilmasin
            daily_shorts_limit=1,
            daily_horizontal_limit=1,
            publish_window_start_hour=0,
            publish_window_end_hour=24,
            stock_max_ready_per_format=5,
        )
        self.db.save_settings(settings)

        # Stokta islenmeyi bekleyen bir shorts
        pending = ClipItem(
            clip_id="flow_pending", source_key="flow_pending",
            platform=PlatformType.TWITCH, channel_name="flowtest",
            title="Pending clip", url="https://clips.twitch.tv/p",
            duration=30.0, view_count=500,
            target_format=ClipTargetFormat.SHORTS, status=ClipStatus.DISCOVERED,
        )
        self.db.save_clip(pending)

        # Onceden hazir bekleyen (daha dusuk hype'li) baska bir shorts
        ready_clip = ClipItem(
            clip_id="flow_ready", source_key="flow_ready",
            platform=PlatformType.TWITCH, channel_name="flowtest",
            title="Ready clip", url="https://clips.twitch.tv/r",
            duration=30.0, hype_score=0.1,
            target_format=ClipTargetFormat.SHORTS, status=ClipStatus.READY,
            processed_video_path=self._make_file(
                os.path.join(self.ready_dir, "flow_ready_shorts.mp4")
            ),
        )
        self.db.save_clip(ready_clip)

        def fake_download(clip):
            raw = self._make_file(os.path.join(self.raw_dir, f"{clip.source_key}.mp4"))
            clip.raw_video_path = raw
            self.db.update_clip_status(clip.clip_id, ClipStatus.DOWNLOADED, raw_video_path=raw)
            return raw

        def fake_process_shorts(clip, settings=None, cut_start_sec=0.0, cut_end_sec=0.0):
            out = self._make_file(os.path.join(self.ready_dir, f"{clip.clip_id}_shorts.mp4"))
            clip.processed_video_path = out
            clip.status = ClipStatus.READY
            self.db.update_clip_status(clip.clip_id, ClipStatus.READY, processed_video_path=out)
            return out

        # 1. Asama: stok isleme
        with patch.object(clip_downloader, "download_clip", side_effect=fake_download), \
             patch.object(clip_editor.clip_editor, "process_clip_to_shorts", side_effect=fake_process_shorts), \
             patch("app.services.clip_scheduler.hype_detector.analyze_audio_volume_peaks", return_value=(5.0, 25.0, 0.9)):
            clip_scheduler._process_stock(settings)

        processed = self.db.get_clip_by_id("flow_pending")
        self.assertEqual(processed.status, ClipStatus.READY)
        self.assertEqual(processed.hype_score, 0.9)

        # 2. Asama: kotali yayinlama (tick) - en yuksek hype'li pending yuklenmeli
        with patch.object(shorts_publisher, "is_youtube_ready", return_value=True), \
             patch("app.services.shorts_publisher.upload_to_youtube", return_value={"id": "test_vid_123"}):
            clip_scheduler._publish_tick(settings)

        published = self.db.get_clip_by_id("flow_pending")
        self.assertEqual(published.status, ClipStatus.PUBLISHED)
        self.assertEqual(published.youtube_video_id, "test_vid_123")
        self.assertIsNotNone(published.published_at)
        self.assertEqual(self.db.published_today_count("shorts"), 1)

        # Kota doldu: ikinci tick hicbir sey yuklememeli
        with patch.object(shorts_publisher, "is_youtube_ready", return_value=True), \
             patch("app.services.shorts_publisher.upload_to_youtube", return_value={"id": "should_not_happen"}) as mock_up:
            clip_scheduler._publish_tick(settings)
            mock_up.assert_not_called()

        # flow_ready hala yayin bekliyor olmali (stokta kaldi)
        still_ready = self.db.get_clip_by_id("flow_ready")
        self.assertEqual(still_ready.status, ClipStatus.READY)

    def test_llm_moment_detection_in_process_shorts(self):
        """settings.llm_moment_detection aktifken LLM ile hook ve viral score cikarilip clip_db'ye kaydedilmeli."""
        clip = ClipItem(
            clip_id="flow_llm_shorts", source_key="flow_llm_shorts",
            platform=PlatformType.TWITCH, channel_name="rraenee",
            title="Inanilmaz Anlar", url="https://clips.twitch.tv/llm",
            transcript="[00:05 -> 00:35] Abi bak simdi buraya geliyoruz ve vurduk!",
            target_format=ClipTargetFormat.SHORTS, status=ClipStatus.DISCOVERED,
        )
        self.db.save_clip(clip)
        raw_path = self._make_file(os.path.join(self.raw_dir, "flow_llm_shorts.mp4"))
        clip.raw_video_path = raw_path

        settings = ClipPipelineSettings(llm_moment_detection=True)

        with patch("app.services.clip_scheduler.hype_detector.get_media_duration", return_value=60.0), \
             patch("app.services.clip_scheduler.hype_detector.detect_viral_moments_llm",
                   return_value=(5.0, 35.0, "İNANILMAZ VURUŞ! 🔥", "Rraenee Şov #shorts", 9)), \
             patch.object(clip_editor.clip_editor, "process_clip_to_shorts") as mock_edit:
            res = clip_scheduler._process_shorts(clip, raw_path, settings, None, None)

        self.assertEqual(res.cut_start_sec, 5.0)
        self.assertEqual(res.cut_end_sec, 35.0)
        self.assertEqual(res.hook_text, "İNANILMAZ VURUŞ! 🔥")
        self.assertEqual(res.viral_score, 9)
        self.assertEqual(res.youtube_title, "Rraenee Şov #shorts")
        self.assertEqual(res.hype_score, 0.9)
        mock_edit.assert_called_once_with(clip, settings=settings, cut_start_sec=5.0, cut_end_sec=35.0)

        # Check DB update
        saved = self.db.get_clip_by_id("flow_llm_shorts")
        self.assertEqual(saved.hook_text, "İNANILMAZ VURUŞ! 🔥")
        self.assertEqual(saved.viral_score, 9)


if __name__ == "__main__":
    unittest.main()
