import os
import sys

root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

import unittest
from datetime import datetime
from unittest.mock import patch

from app.models.clip_schema import ClipPipelineSettings, ClipTargetFormat
from app.services.clip_publisher import clip_publisher


class TestClipPublisherQuota(unittest.TestCase):
    def test_daily_limit_values(self):
        settings = ClipPipelineSettings(daily_shorts_limit=3, daily_horizontal_limit=2)
        self.assertEqual(clip_publisher.daily_limit(ClipTargetFormat.SHORTS, settings), 3)
        self.assertEqual(clip_publisher.daily_limit(ClipTargetFormat.HORIZONTAL, settings), 2)

    def test_publish_window_normal(self):
        settings = ClipPipelineSettings(publish_window_start_hour=9, publish_window_end_hour=22)
        self.assertTrue(clip_publisher.is_within_publish_window(settings, now=datetime(2026, 1, 1, 10, 0)))
        self.assertTrue(clip_publisher.is_within_publish_window(settings, now=datetime(2026, 1, 1, 9, 0)))
        self.assertFalse(clip_publisher.is_within_publish_window(settings, now=datetime(2026, 1, 1, 8, 59)))
        self.assertFalse(clip_publisher.is_within_publish_window(settings, now=datetime(2026, 1, 1, 22, 0)))
        self.assertFalse(clip_publisher.is_within_publish_window(settings, now=datetime(2026, 1, 1, 23, 30)))

    def test_publish_window_wraps_midnight(self):
        settings = ClipPipelineSettings(publish_window_start_hour=22, publish_window_end_hour=2)
        self.assertTrue(clip_publisher.is_within_publish_window(settings, now=datetime(2026, 1, 1, 23, 0)))
        self.assertTrue(clip_publisher.is_within_publish_window(settings, now=datetime(2026, 1, 1, 1, 0)))
        self.assertFalse(clip_publisher.is_within_publish_window(settings, now=datetime(2026, 1, 1, 12, 0)))

    def test_quota_remaining(self):
        settings = ClipPipelineSettings(daily_shorts_limit=3)
        with patch.object(clip_publisher, "published_today", return_value=2):
            self.assertEqual(clip_publisher.quota_remaining(ClipTargetFormat.SHORTS, settings), 1)
        with patch.object(clip_publisher, "published_today", return_value=5):
            self.assertEqual(clip_publisher.quota_remaining(ClipTargetFormat.SHORTS, settings), 0)

    def test_run_publish_tick_disabled(self):
        settings = ClipPipelineSettings(auto_upload=False)
        result = clip_publisher.run_publish_tick(settings)
        self.assertEqual(result["published"], [])
        self.assertIn("reason", result["skipped"])

    def test_run_publish_tick_outside_window(self):
        settings = ClipPipelineSettings(auto_upload=True)
        with patch.object(clip_publisher, "is_within_publish_window", return_value=False):
            result = clip_publisher.run_publish_tick(settings)
        self.assertEqual(result["published"], [])
        self.assertIn("reason", result["skipped"])

    def test_run_publish_tick_publishes_per_format(self):
        """Kota musait oldugunda her format icin birer video yuklenmeli."""
        settings = ClipPipelineSettings(
            auto_upload=True,
            daily_shorts_limit=3,
            daily_horizontal_limit=3,
        )
        fake_info = {"clip_id": "x", "title": "t", "youtube_video_id": "vid123"}

        with patch.object(clip_publisher, "is_within_publish_window", return_value=True), \
             patch.object(clip_publisher, "published_today", return_value=0), \
             patch.object(clip_publisher, "publish_next_ready", return_value=fake_info) as mock_publish:
            result = clip_publisher.run_publish_tick(settings)

        self.assertEqual(len(result["published"]), 2)
        self.assertEqual(mock_publish.call_count, 2)
        formats_called = {call.args[0] for call in mock_publish.call_args_list}
        self.assertEqual(formats_called, {ClipTargetFormat.SHORTS, ClipTargetFormat.HORIZONTAL})

    def test_run_publish_tick_respects_quota(self):
        """Kota dolduysa hic yukleme yapilmamali."""
        settings = ClipPipelineSettings(auto_upload=True, daily_shorts_limit=3, daily_horizontal_limit=3)

        with patch.object(clip_publisher, "is_within_publish_window", return_value=True), \
             patch.object(clip_publisher, "published_today", return_value=3), \
             patch.object(clip_publisher, "publish_next_ready", return_value=None) as mock_publish:
            result = clip_publisher.run_publish_tick(settings)

        self.assertEqual(result["published"], [])
        self.assertEqual(mock_publish.call_count, 0)


if __name__ == "__main__":
    unittest.main()
