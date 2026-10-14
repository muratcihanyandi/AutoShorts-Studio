import os
import sys
import subprocess

root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

import unittest
from app.models.clip_schema import (
    PlatformType, ClipStatus, ClipItem, ClipPipelineSettings,
    ClipTargetFormat, ClipSourceKind,
)
from app.services.clip_editor import clip_editor
from app.services.hype_detector import hype_detector
from app.services.clip_db import clip_db


class TestClipHorizontalRender(unittest.TestCase):
    def setUp(self):
        self.temp_video = os.path.join(root_dir, "storage", "clips", "raw", "test_horizontal_16x9.mp4")
        os.makedirs(os.path.dirname(self.temp_video), exist_ok=True)

        # 12 saniyelik sentetik 16:9 video (parcalama testi icin yeterli uzunluk)
        ffmpeg_bin = clip_editor.ffmpeg_bin
        cmd = [
            ffmpeg_bin, "-y",
            "-f", "lavfi", "-i", "testsrc=duration=12:size=1280x720:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=12",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            self.temp_video
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertTrue(os.path.exists(self.temp_video), f"Test video creation failed: {res.stderr}")

        self.created_clip_ids = []

    def tearDown(self):
        if os.path.exists(self.temp_video):
            try:
                os.remove(self.temp_video)
            except Exception:
                pass
        for clip_id in self.created_clip_ids:
            try:
                clip = clip_db.get_clip_by_id(clip_id)
                if clip and clip.processed_video_path and os.path.exists(clip.processed_video_path):
                    os.remove(clip.processed_video_path)
            except Exception:
                pass
            clip_db.delete_clip(clip_id)

    def _probe(self, path: str):
        ffprobe_bin = hype_detector.ffprobe_bin
        cmd = [
            ffprobe_bin, "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=s=x:p=0",
            path
        ]
        return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.strip()

    def _probe_duration(self, path: str):
        ffprobe_bin = hype_detector.ffprobe_bin
        cmd = [
            ffprobe_bin, "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            path
        ]
        return float(subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.strip())

    def test_horizontal_single_render_keeps_aspect(self):
        """Kisa icerik tek yatay video olmali; cozunurluk 16:9 korunmali ve kesim uygulanmali."""
        clip = ClipItem(
            clip_id="test_horiz_single",
            source_key="test_horiz_single",
            platform=PlatformType.TWITCH,
            channel_name="streamer",
            title="Yatay tek",
            url="https://test.com/vod",
            duration=12.0,
            raw_video_path=self.temp_video,
            target_format=ClipTargetFormat.HORIZONTAL,
            source_kind=ClipSourceKind.CLIP,
            status=ClipStatus.DOWNLOADED
        )
        clip_db.save_clip(clip)
        self.created_clip_ids.append(clip.clip_id)

        settings = ClipPipelineSettings()
        ready_items = clip_editor.process_clip_horizontal(
            clip, settings=settings, cut_start_sec=2.0, cut_end_sec=10.0
        )

        self.assertEqual(len(ready_items), 1)
        out = ready_items[0].processed_video_path
        self.assertTrue(os.path.exists(out))
        self.assertGreater(os.path.getsize(out), 1000)

        # En-boy orani degismemeli (altyazi da yok)
        self.assertEqual(self._probe(out), "1280x720")

        # Kesim uygulanmis olmali (~8 saniye)
        duration = self._probe_duration(out)
        self.assertGreater(duration, 6.5)
        self.assertLess(duration, 9.5)

        # Durum READY ve DB'de islenmis gorunmeli
        updated = clip_db.get_clip_by_id(clip.clip_id)
        self.assertEqual(updated.status, ClipStatus.READY)

    def test_vod_split_into_parts(self):
        """Uzun VOD, max parca suresine gore parcalara bolunmeli ve parcalar READY olmali."""
        clip = ClipItem(
            clip_id="test_horiz_vod",
            source_key="test_horiz_vod",
            platform=PlatformType.TWITCH,
            channel_name="streamer",
            title="Test VOD",
            url="https://test.com/vod2",
            duration=12.0,
            raw_video_path=self.temp_video,
            target_format=ClipTargetFormat.HORIZONTAL,
            source_kind=ClipSourceKind.VOD,
            status=ClipStatus.DOWNLOADED
        )
        clip_db.save_clip(clip)
        self.created_clip_ids.append(clip.clip_id)

        # 0.1 dakika = 6 saniye -> 12 sn govde 2 parcaya bolunmeli
        settings = ClipPipelineSettings(horizontal_max_part_minutes=0.1)
        ready_items = clip_editor.process_clip_horizontal(
            clip, settings=settings, cut_start_sec=0.0, cut_end_sec=12.0
        )

        self.assertEqual(len(ready_items), 2)
        part_ids = [i.clip_id for i in ready_items]
        self.created_clip_ids.extend(part_ids)
        self.assertIn("test_horiz_vod_p1", part_ids)
        self.assertIn("test_horiz_vod_p2", part_ids)

        for part in ready_items:
            self.assertEqual(part.status, ClipStatus.READY)
            self.assertEqual(part.target_format, ClipTargetFormat.HORIZONTAL)
            self.assertEqual(part.source_kind, ClipSourceKind.VOD_PART)
            self.assertEqual(part.source_key, "test_horiz_vod")
            self.assertTrue(os.path.exists(part.processed_video_path))
            self.assertEqual(self._probe(part.processed_video_path), "1280x720")

            db_part = clip_db.get_clip_by_id(part.clip_id)
            self.assertIsNotNone(db_part)
            self.assertEqual(db_part.status, ClipStatus.READY)

        # Parent SPLIT olmali ve parca kayitlari parent ile ayni kaynagi paylasmali
        parent = clip_db.get_clip_by_id("test_horiz_vod")
        self.assertEqual(parent.status, ClipStatus.SPLIT)


if __name__ == "__main__":
    unittest.main()
