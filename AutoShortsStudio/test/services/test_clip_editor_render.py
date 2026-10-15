import os
import sys
import subprocess

root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

import unittest
import uuid
from unittest.mock import patch
from app.models.clip_schema import PlatformType, ClipStatus, ClipItem, ClipPipelineSettings
from app.services.clip_editor import clip_editor
from app.services.hype_detector import hype_detector
from app.services.clip_db import ClipDatabase


class TestClipEditorRender(unittest.TestCase):
    def setUp(self):
        self.db_name = os.path.join(root_dir, "storage", f"test_render_{uuid.uuid4().hex[:8]}.db")
        self.test_db = ClipDatabase(self.db_name)
        self.db_patcher = patch("app.services.clip_editor.clip_db", self.test_db)
        self.db_patcher.start()

        self.temp_video = os.path.join(root_dir, "storage", "clips", "raw", "test_sample_16x9.mp4")
        os.makedirs(os.path.dirname(self.temp_video), exist_ok=True)
        
        # FFmpeg ile 5 saniyelik sentetik 16:9 1280x720 test videosu ve bip sesi olustur
        ffmpeg_bin = clip_editor.ffmpeg_bin
        cmd = [
            ffmpeg_bin, "-y",
            "-f", "lavfi", "-i", "testsrc=duration=5:size=1280x720:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=1000:duration=5",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            self.temp_video
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertTrue(os.path.exists(self.temp_video), f"Test video creation failed: {res.stderr}")

    def tearDown(self):
        if hasattr(self, "db_patcher"):
            self.db_patcher.stop()
        if hasattr(self, "db_name") and os.path.exists(self.db_name):
            try:
                os.remove(self.db_name)
            except Exception:
                pass
        if os.path.exists(self.temp_video):
            try:
                os.remove(self.temp_video)
            except Exception:
                pass

    def test_9_16_blur_bg_render(self):
        """16:9 videonun 9:16 Shorts formatina donusturuldugunu dogrula."""
        clip = ClipItem(
            clip_id="test_render_sample",
            platform=PlatformType.TWITCH,
            channel_name="streamer",
            title="Epic Moment",
            url="https://test.com/clip",
            raw_video_path=self.temp_video,
            status=ClipStatus.DOWNLOADED
        )
        self.test_db.save_clip(clip)

        settings = ClipPipelineSettings(
            video_layout="blur_bg",
            subtitle_enabled=False # Sentetik seste konusma olmadigi icin whisper bypass
        )

        out_shorts = clip_editor.process_clip_to_shorts(
            clip,
            settings=settings,
            cut_start_sec=1.0,
            cut_end_sec=4.0
        )

        self.assertTrue(os.path.exists(out_shorts))
        self.assertGreater(os.path.getsize(out_shorts), 1000)

        # ffprobe ile cozunurlugun 1080x1920 oldugunu dogrula
        ffprobe_bin = hype_detector.ffprobe_bin
        cmd = [
            ffprobe_bin, "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=s=x:p=0",
            out_shorts
        ]
        probe_res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        res_text = probe_res.stdout.strip()
        self.assertEqual(res_text, "1080x1920")

        # Temizlik
        if os.path.exists(out_shorts):
            try:
                os.remove(out_shorts)
            except Exception:
                pass

    def test_hook_overlay_generation(self):
        """Pillow ile kanca görseli üretimi testi."""
        hook_path = os.path.join(root_dir, "storage", "clips", "temp", "test_hook_render.png")
        os.makedirs(os.path.dirname(hook_path), exist_ok=True)
        res = clip_editor.generate_hook_overlay_image(
            text="Rraenee Sinir Krizi Geçirdi 💀",
            out_png_path=hook_path
        )
        self.assertIsNotNone(res)
        self.assertTrue(os.path.exists(hook_path))
        self.assertGreater(os.path.getsize(hook_path), 500)
        if os.path.exists(hook_path):
            try:
                os.remove(hook_path)
            except Exception:
                pass

    def test_streamer_facecam_render_with_hook(self):
        """OpenShorts streamer_facecam düzeni ve hook overlay ile render testi."""
        clip = ClipItem(
            clip_id="test_render_facecam",
            platform=PlatformType.YOUTUBE,
            channel_name="rraenee",
            title="Rraenee Komik Klip",
            url="https://youtube.com/watch?v=123",
            raw_video_path=self.temp_video,
            hook_text="Rraenee Çıldırdı 😱",
            status=ClipStatus.DOWNLOADED
        )
        self.test_db.save_clip(clip)

        settings = ClipPipelineSettings(
            video_layout="streamer_facecam",
            hook_overlay_enabled=True,
            subtitle_enabled=False
        )

        out_shorts = clip_editor.process_clip_to_shorts(
            clip,
            settings=settings,
            cut_start_sec=0.5,
            cut_end_sec=3.5
        )

        self.assertTrue(os.path.exists(out_shorts))
        self.assertGreater(os.path.getsize(out_shorts), 1000)

        # Çözünürlük 1080x1920 olmalı
        ffprobe_bin = hype_detector.ffprobe_bin
        cmd = [
            ffprobe_bin, "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=s=x:p=0",
            out_shorts
        ]
        probe_res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        self.assertEqual(probe_res.stdout.strip(), "1080x1920")

        if os.path.exists(out_shorts):
            try:
                os.remove(out_shorts)
            except Exception:
                pass

    def test_auto_facecam_detection(self):
        """Otomatik webcam/facecam tespiti ve auto layout render testi."""
        detected = clip_editor.detect_facecam_position(self.temp_video)
        self.assertIn(detected, ["bottom_left", "bottom_right", "top_left", "top_right"])

        clip = ClipItem(
            clip_id="test_auto_facecam_render",
            platform=PlatformType.TWITCH,
            channel_name="streamer",
            title="Auto Facecam Test",
            url="https://twitch.tv/clip/123",
            raw_video_path=self.temp_video,
            status=ClipStatus.DOWNLOADED
        )
        self.test_db.save_clip(clip)

        settings = ClipPipelineSettings(
            video_layout="streamer_facecam",
            facecam_position="auto",
            subtitle_enabled=False
        )

        out_shorts = clip_editor.process_clip_to_shorts(
            clip,
            settings=settings,
            cut_start_sec=1.0,
            cut_end_sec=3.0
        )
        self.assertTrue(os.path.exists(out_shorts))
        if os.path.exists(out_shorts):
            try:
                os.remove(out_shorts)
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main()

